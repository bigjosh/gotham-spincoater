"""Hardware-free recipe state machine and independent RPM seekers.

Call update() on the control core, then apply its six requested duties. A
snapshot is detached data, suitable for publishing to the UI/network core.
"""

try:
    from time import ticks_diff
except ImportError:
    def ticks_diff(new, old):
        return (new - old + (1 << 29)) % (1 << 30) - (1 << 29)

from recipes import DEFAULT_SETTINGS, validate_profile, validate_settings


class ControlEngine:
    # Percentage points per second per RPM error. Conservative integral
    # seeking limits hunting while the fan and eight-period average respond.
    SEEK_GAIN = 0.01
    MAX_CONTROL_DT_S = 0.1
    STARTUP_DUTY_LIMIT = 30.0
    QUIET_S = 1.5

    def __init__(self, enabled=(0,), settings=None):
        if (len(set(enabled)) != len(enabled)
                or any(type(channel) is not int or not 0 <= channel < 6
                       for channel in enabled)):
            raise ValueError("enabled channels must be unique integers from 0 to 5")
        chosen_settings = validate_settings(DEFAULT_SETTINGS if settings is None else settings)
        self.enabled = tuple(enabled)
        self.duties = [0.0] * 6
        self.state = "IDLE"
        self.target_rpm = 0.0
        self.message = "Ready" if self.enabled else "Enable at least one fan before START"
        self._profile = None
        self._settings = chosen_settings
        self._index = 0
        self._now_ms = 0
        self._last_ms = None
        self._elapsed_ms = 0
        self._phase_elapsed_ms = 0
        self._from_rpm = 0.0
        self._seen_tach = [False] * 6
        self._last_pulse_ms = [None] * 6
        self._pulse_counts = [None] * 6
        self._readings = [{} for _ in range(6)]
        self._standstill_assumed = False
        self._blind_coast = False
        self._clear_warnings()

    @property
    def running(self):
        return self.state in ("RAMP", "DWELL")

    @property
    def participating(self):
        """RPM deviations never remove a selected channel from the run."""
        return self.enabled

    def start(self, profile, settings, now_ms):
        if self.running:
            raise ValueError("stop the current run before starting another")
        if not self.enabled:
            raise ValueError("Enable at least one fan before START")
        self._profile = validate_profile(profile)
        self._settings = validate_settings(settings)
        self.duties = [0.0] * 6
        self.target_rpm = 0.0
        self._now_ms = now_ms
        self._last_ms = now_ms
        self._elapsed_ms = 0
        self._seen_tach = [False] * 6
        self._last_pulse_ms = [now_ms] * 6
        self._pulse_counts = [None] * 6
        self._readings = [{} for _ in range(6)]
        self._standstill_assumed = False
        self._blind_coast = False
        self._clear_warnings()
        self._enter_step(0)
        self._advance_schedule(0)
        self._update_warnings()
        return self.snapshot()

    def set_enabled(self, enabled):
        """Control-core-only idle reconfiguration; never starts an output."""
        if self.running:
            raise RuntimeError("Stop the recipe before changing fans")
        # Validate before replacing any current state. Disabled channels lose
        # stale measurements, duty and settling history at the same boundary.
        self.__init__(enabled, self._settings)
        return self.snapshot()

    def configure_settings(self, settings):
        """Apply display/control settings on the worker while no recipe runs."""
        if self.running:
            raise RuntimeError("Stop the recipe before changing settings")
        self._settings = validate_settings(settings)
        return self.snapshot()

    def stop(self, reason="Stopped"):
        self.duties = [0.0] * 6
        self.target_rpm = 0.0
        self.state = "STOPPED"
        self.message = str(reason)
        self._blind_coast = False
        self._standstill_assumed = False
        self._clear_warnings()
        return self.snapshot()

    def _clear_warnings(self):
        self._outside_since = [None] * 6
        self._out_of_bounds_s = [0.0] * 6
        self._in_bounds = [False] * 6
        self._warnings = [False] * 6
        self._error_percent = [None] * 6

    def _zero_in_bounds(self, channel, reading):
        if reading.get("driver_error"):
            return False
        if reading.get("valid", False):
            rpm = reading["rpm"]
            return rpm == 0 or rpm < self._settings["rpm_zero_threshold"]
        quiet_since = self._last_pulse_ms[channel]
        quiet = (quiet_since is not None
                 and ticks_diff(self._now_ms, quiet_since) >= self.QUIET_S * 1000)
        self._standstill_assumed = self._standstill_assumed or quiet
        return quiet

    def _update_warnings(self):
        if not self.running:
            self._clear_warnings()
            return
        target = self.target_rpm
        for channel in self.enabled:
            reading = self._readings[channel]
            error_percent = None
            if target == 0:
                in_bounds = self._zero_in_bounds(channel, reading)
            else:
                if reading.get("valid", False):
                    error_percent = abs(reading["rpm"] - target) * 100.0 / target
                    in_bounds = error_percent <= self._settings["rpm_warning_percent"]
                else:
                    in_bounds = False
            self._error_percent[channel] = error_percent
            self._in_bounds[channel] = bool(in_bounds)
            if in_bounds:
                self._outside_since[channel] = None
                self._out_of_bounds_s[channel] = 0.0
                self._warnings[channel] = False
            else:
                if self._outside_since[channel] is None:
                    self._outside_since[channel] = self._now_ms
                outside = max(0, ticks_diff(self._now_ms, self._outside_since[channel])) / 1000.0
                self._out_of_bounds_s[channel] = outside
                self._warnings[channel] = outside >= self._settings["rpm_warning_delay_s"]

    def _enter_step(self, index):
        self._from_rpm = self.target_rpm
        self._index = index
        self._phase_elapsed_ms = 0
        step = self._profile["steps"][index]
        self.state = "RAMP" if step["slew_s"] > 0 else "DWELL"
        if self.state == "DWELL":
            self.target_rpm = float(step["rpm"])
        self.message = "Ramping" if self.state == "RAMP" else "Holding target"

    def _advance(self):
        if self._index + 1 == len(self._profile["steps"]):
            self.state = "COMPLETE"
            self.target_rpm = 0.0
            self.duties = [0.0] * 6
            self.message = "Recipe complete"
            self._blind_coast = False
            self._standstill_assumed = False
        else:
            self._enter_step(self._index + 1)

    def _advance_schedule(self, elapsed_ms):
        """Consume wall time without issuing intermediate power commands.

        At most two phases per step are crossed, including zero-duration
        steps. A late tick seeks only the target due now, never old targets.
        Integer milliseconds prevent cumulative float rounding over long runs.
        """
        while self.running:
            step = self._profile["steps"][self._index]
            self.message = "Ramping" if self.state == "RAMP" else "Holding target"
            duration = step["slew_s"] if self.state == "RAMP" else step["dwell_s"]
            duration_ms = round(duration * 1000)
            remaining_ms = max(0, duration_ms - self._phase_elapsed_ms)
            if elapsed_ms < remaining_ms:
                self._phase_elapsed_ms += elapsed_ms
                if self.state == "RAMP":
                    fraction = self._phase_elapsed_ms / duration_ms
                    self.target_rpm = self._from_rpm + (step["rpm"] - self._from_rpm) * fraction
                return
            elapsed_ms = max(0, elapsed_ms - remaining_ms)
            if self.state == "RAMP":
                self.target_rpm = float(step["rpm"])
                self.state = "DWELL"
                self._phase_elapsed_ms = 0
                self.message = "Holding target"
            else:
                self._advance()

    def _capture(self, readings):
        emergency = False
        for channel in range(6):
            reading = readings.get(channel, {})
            if self._last_pulse_ms[channel] is None:
                self._last_pulse_ms[channel] = self._now_ms
            rpm = reading.get("rpm")
            valid = (reading.get("valid") is True
                     and not reading.get("driver_error") and not reading.get("fault")
                     and not isinstance(rpm, bool)
                     and isinstance(rpm, (int, float))
                     and 0 <= rpm < float("inf"))
            overflow = bool(reading.get("overflow", False))
            if overflow:
                # A discarded full FIFO proves tach activity even though its
                # periods never reach the accepted-pulse total. It cannot be
                # interpreted as a quiet shaft at either zero endpoint.
                valid = False
                self._last_pulse_ms[channel] = self._now_ms
            age = reading.get("age_ms")
            if age is not None and (not isinstance(age, (int, float)) or age < 0
                                    or age >= self.QUIET_S * 1000):
                valid = False
            self._readings[channel] = {
                "rpm": float(rpm) if valid else None,
                "valid": bool(valid), "age_ms": age,
                "driver_error": reading.get("driver_error") or reading.get("fault"),
            }
            pulses = reading.get("pulses")
            if pulses is not None:
                if self._pulse_counts[channel] != pulses:
                    self._last_pulse_ms[channel] = self._now_ms
                self._pulse_counts[channel] = pulses
            elif valid:
                self._last_pulse_ms[channel] = self._now_ms
            # Runtime owns driver errors. A PIO driver error can also park its
            # output; that stopped flag does not represent the shared STOP.
            if (self.running and channel in self.enabled
                    and not reading.get("fault") and not reading.get("driver_error")):
                emergency = emergency or bool(reading.get("stopped", False))
        return emergency

    def _regulate(self, control_dt):
        target = self.target_rpm
        self._standstill_assumed = False
        self._blind_coast = False
        for channel in self.participating:
            reading = self._readings[channel]
            valid = reading["valid"]
            if reading.get("driver_error"):
                self.duties[channel] = 0.0
                continue
            if target == 0:
                self.duties[channel] = 0.0
                continue

            if valid:
                self._seen_tach[channel] = True
                error = target - reading["rpm"]
                near = abs(error) <= self._settings["tolerance_rpm"]
                rate = 0.0 if near else error * self.SEEK_GAIN
            else:
                step = self._profile["steps"][self._index]
                blind_coast = (self.state == "RAMP" and step["rpm"] == 0
                               and self._from_rpm > 0
                               and self.duties[channel] <= self.STARTUP_DUTY_LIMIT)
                # Initial spin-up is bounded. Once measured, hold power during
                # absent tach rather than treating missing RPM as zero.
                if blind_coast:
                    # A fan can stop below its minimum running speed before a
                    # slow zero-endpoint ramp finishes. With <=30% requested
                    # power, keep removing power; never chase absent tach up.
                    rate = -self._settings["max_power_per_s"]
                    self._blind_coast = True
                else:
                    rate = 0.0 if self._seen_tach[channel] else target * self.SEEK_GAIN
            rate_limit = self._settings["max_power_per_s"]
            rate = max(-rate_limit, min(rate_limit, rate))
            duty = max(0.0, min(100.0, self.duties[channel] + rate * control_dt))
            if not self._seen_tach[channel]:
                duty = min(self.STARTUP_DUTY_LIMIT, duty)
            self.duties[channel] = duty
        if self._blind_coast:
            self.message = "Coasting toward stop; tach absent"

    def update(self, now_ms, readings):
        self._now_ms = now_ms
        elapsed_ms = 0 if self._last_ms is None else max(0, ticks_diff(now_ms, self._last_ms))
        self._last_ms = now_ms
        emergency = self._capture(readings)
        if emergency:
            # stop() latches each PIO channel. Its next sample must not erase
            # the explicit reason for a software STOP.
            if self.state != "STOPPED":
                self.stop("Hardware emergency stop")
            return list(self.duties)
        if not self.running:
            return list(self.duties)
        self._elapsed_ms += elapsed_ms
        control_dt = min(elapsed_ms / 1000.0, self.MAX_CONTROL_DT_S)
        self._advance_schedule(elapsed_ms)
        if self.running:
            self._regulate(control_dt)
        self._update_warnings()
        return list(self.duties)

    def snapshot(self):
        remaining = 0.0
        if self._profile:
            step = self._profile["steps"][self._index]
            if self.state == "RAMP":
                remaining = max(0.0, step["slew_s"] - self._phase_elapsed_ms / 1000.0)
            elif self.state == "DWELL":
                remaining = max(0.0, step["dwell_s"] - self._phase_elapsed_ms / 1000.0)
        fans = []
        for channel in range(6):
            reading = self._readings[channel]
            valid = reading.get("valid", False)
            rpm = reading.get("rpm") if valid else None
            display_rpm = (rpm if valid and rpm >= self._settings["rpm_zero_threshold"] else 0)
            participating = channel in self.participating
            target = self.target_rpm if participating else 0
            fans.append({"enabled": channel in self.enabled, "rpm": rpm,
                         "display_rpm": display_rpm,
                         "valid": valid, "duty": self.duties[channel],
                         "participating": participating,
                         "warning": self._warnings[channel],
                         "in_bounds": self._in_bounds[channel],
                         "out_of_bounds_s": self._out_of_bounds_s[channel],
                         "error_percent": self._error_percent[channel],
                         "target_rpm": target,
                         "error": target - rpm if valid else None})
        return {
            "state": self.state, "running": self.running,
            "target_rpm": self.target_rpm,
            "step": self._index + 1 if self._profile else 0,
            "step_count": len(self._profile["steps"]) if self._profile else 0,
            "phase_remaining_s": remaining,
            "recipe_name": self._profile["name"] if self._profile else "",
            "message": self.message, "elapsed_s": self._elapsed_ms / 1000.0,
            "standstill_assumed": self._standstill_assumed,
            "coasting_without_tach": self._blind_coast,
            "warning_count": sum(self._warnings),
            "fans": fans,
        }
