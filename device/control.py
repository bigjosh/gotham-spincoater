"""Hardware-free recipe state machine and independent RPM seekers.

Call update() on the control core, then apply its six requested duties. A
snapshot is detached data, suitable for publishing to the UI/network core.
"""

try:
    from time import ticks_diff
except ImportError:
    def ticks_diff(new, old):
        return (new - old + (1 << 29)) % (1 << 30) - (1 << 29)

from recipes import validate_profile, validate_settings


class ControlEngine:
    # Percentage points per second per RPM error. Conservative integral
    # seeking limits hunting while the fan and eight-period average respond.
    SEEK_GAIN = 0.01
    MAX_CONTROL_DT_S = 0.1
    STARTUP_TACH_GRACE_S = 8.0
    STARTUP_DUTY_LIMIT = 30.0
    LOST_TACH_GRACE_S = 1.5
    QUIET_S = 1.5

    def __init__(self, enabled=(0,)):
        if (len(set(enabled)) != len(enabled)
                or any(type(channel) is not int or not 0 <= channel < 6
                       for channel in enabled)):
            raise ValueError("enabled channels must be unique integers from 0 to 5")
        self.enabled = tuple(enabled)
        self.duties = [0.0] * 6
        self.faults = [None] * 6
        self.state = "IDLE"
        self.target_rpm = 0.0
        self.message = "Ready" if self.enabled else "Enable at least one fan before START"
        self._profile = None
        self._settings = None
        self._index = 0
        self._now_ms = 0
        self._last_ms = None
        self._elapsed = 0.0
        self._phase_elapsed = 0.0
        self._within = [0.0] * 6
        self._outside = [0.0] * 6
        self._near = [False] * 6
        self._dwell = 0.0
        self._from_rpm = 0.0
        self._seen_tach = [False] * 6
        self._missing = [0.0] * 6
        self._zero_since = [None] * 6
        self._last_pulse_ms = [None] * 6
        self._pulse_counts = [None] * 6
        self._readings = [{} for _ in range(6)]
        self._standstill_assumed = False
        self._blind_coast = False

    @property
    def running(self):
        return self.state in ("RAMP", "SETTLE", "DWELL")

    @property
    def participating(self):
        """Selected channels still allowed to run in this attempt."""
        return tuple(channel for channel in self.enabled if self.faults[channel] is None)

    def start(self, profile, settings, now_ms):
        if self.running:
            raise ValueError("stop the current run before starting another")
        if not self.enabled:
            raise ValueError("Enable at least one fan before START")
        self._profile = validate_profile(profile)
        self._settings = validate_settings(settings)
        self.duties = [0.0] * 6
        self.faults = [None] * 6
        self.target_rpm = 0.0
        self._now_ms = now_ms
        self._last_ms = now_ms
        self._elapsed = 0.0
        self._seen_tach = [False] * 6
        self._missing = [0.0] * 6
        self._zero_since = [now_ms] * 6
        self._last_pulse_ms = [now_ms] * 6
        self._pulse_counts = [None] * 6
        self._readings = [{} for _ in range(6)]
        self._standstill_assumed = False
        self._blind_coast = False
        self._enter_step(0)
        return self.snapshot()

    def set_enabled(self, enabled):
        """Control-core-only idle reconfiguration; never starts an output."""
        if self.running:
            raise RuntimeError("Stop the recipe before changing fans")
        # Validate before replacing any current state. Disabled channels lose
        # stale measurements, duty and settling history at the same boundary.
        self.__init__(enabled)
        return self.snapshot()

    def stop(self, reason="Stopped"):
        self.duties = [0.0] * 6
        self.target_rpm = 0.0
        self.state = "STOPPED"
        self.message = str(reason)
        self._blind_coast = False
        return self.snapshot()

    def _fault(self, reason):
        self.stop(reason)
        self.state = "FAULT"

    def fault_channel(self, channel, reason):
        """Latch one channel out until START, preserving the saved selection."""
        if channel not in self.enabled or self.faults[channel] is not None:
            return
        self.faults[channel] = str(reason)
        self.duties[channel] = 0.0
        self._readings[channel] = {}
        self._near[channel] = False
        if not self.participating:
            self._fault("Fan %d: %s; no fans remaining" % (channel, reason))

    def _enter_step(self, index):
        self._from_rpm = self.target_rpm
        self._index = index
        self._phase_elapsed = 0.0
        self._within = [0.0] * 6
        self._outside = [0.0] * 6
        self._near = [False] * 6
        self._dwell = 0.0
        step = self._profile["steps"][index]
        self.state = "RAMP" if step["slew_s"] > 0 else "SETTLE"
        if self.state == "SETTLE":
            self.target_rpm = float(step["rpm"])
        self.message = "Ramping" if self.state == "RAMP" else "Waiting for all fans"

    def _advance(self):
        if self._index + 1 == len(self._profile["steps"]):
            self.state = "COMPLETE"
            self.target_rpm = 0.0
            self.duties = [0.0] * 6
            failed = sum(fault is not None for fault in self.faults)
            self.message = ("Recipe complete; %d fan%s faulted" %
                            (failed, "" if failed == 1 else "s")
                            if failed else "Recipe complete")
        else:
            self._enter_step(self._index + 1)

    def _capture(self, readings):
        emergency = False
        for channel in self.participating:
            reading = readings.get(channel, {})
            rpm = reading.get("rpm")
            valid = (reading.get("valid") is True
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
            }
            pulses = reading.get("pulses")
            if pulses is not None:
                if self._pulse_counts[channel] != pulses:
                    self._last_pulse_ms[channel] = self._now_ms
                self._pulse_counts[channel] = pulses
            elif valid:
                self._last_pulse_ms[channel] = self._now_ms
            # A driver fault also parks its PIO state machine. That stopped
            # flag belongs to this channel, not the shared physical STOP.
            if reading.get("fault"):
                self.fault_channel(channel, reading["fault"])
            else:
                emergency = emergency or bool(reading.get("stopped", False))
        return emergency

    def _regulate(self, elapsed, control_dt):
        target = self.target_rpm
        self._standstill_assumed = False
        self._blind_coast = False
        for channel in self.participating:
            reading = self._readings[channel]
            valid = reading["valid"]
            if target == 0:
                self.duties[channel] = 0.0
                self._missing[channel] = 0.0
                if self._zero_since[channel] is None:
                    self._zero_since[channel] = self._now_ms
                if valid:
                    near = reading["rpm"] <= self._settings["tolerance_rpm"]
                else:
                    quiet_since = self._last_pulse_ms[channel]
                    zero_since = self._zero_since[channel]
                    quiet = (quiet_since is not None
                             and ticks_diff(self._now_ms, quiet_since) >= self.QUIET_S * 1000
                             and ticks_diff(self._now_ms, zero_since) >= self.QUIET_S * 1000)
                    near = quiet
                    self._standstill_assumed = self._standstill_assumed or quiet
                self._near[channel] = near
                continue

            self._zero_since[channel] = None
            if valid:
                self._seen_tach[channel] = True
                self._missing[channel] = 0.0
                error = target - reading["rpm"]
                near = abs(error) <= self._settings["tolerance_rpm"]
                rate = 0.0 if near else error * self.SEEK_GAIN
            else:
                self._missing[channel] += elapsed
                step = self._profile["steps"][self._index]
                blind_coast = (self.state == "RAMP" and step["rpm"] == 0
                               and self._from_rpm > 0
                               and self.duties[channel] <= self.STARTUP_DUTY_LIMIT)
                grace = (self.LOST_TACH_GRACE_S if self._seen_tach[channel]
                         else self.STARTUP_TACH_GRACE_S)
                if not blind_coast and self._missing[channel] >= grace:
                    self.fault_channel(channel, "tach signal missing")
                    continue
                near = False
                # Initial spin-up is bounded. Once measured, hold power during
                # a short dropout rather than treating missing RPM as zero.
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
            self._near[channel] = near
        if self._blind_coast:
            self.message = "Coasting toward stop; tach absent"
        return bool(self.participating) and all(self._near[i] for i in self.participating)

    def update(self, now_ms, readings):
        self._now_ms = now_ms
        elapsed = 0.0 if self._last_ms is None else max(0, ticks_diff(now_ms, self._last_ms)) / 1000.0
        self._last_ms = now_ms
        emergency = self._capture(readings)
        if emergency:
            # stop() latches each PIO channel. Its next sample must not erase
            # the original fault or the explicit reason for a software STOP.
            if self.state not in ("FAULT", "STOPPED"):
                self.stop("Hardware emergency stop")
            return list(self.duties)
        if not self.running:
            return list(self.duties)
        self._elapsed += elapsed
        control_dt = min(elapsed, self.MAX_CONTROL_DT_S)
        step = self._profile["steps"][self._index]
        state_at_start = self.state
        if self.state == "RAMP":
            self.message = "Ramping"
            self._phase_elapsed += elapsed
            fraction = min(1.0, self._phase_elapsed / step["slew_s"])
            self.target_rpm = self._from_rpm + (step["rpm"] - self._from_rpm) * fraction
            if fraction >= 1:
                self.target_rpm = float(step["rpm"])
                self.state = "SETTLE"
                self._phase_elapsed = 0.0
                self.message = "Waiting for all fans"
        within = self._regulate(elapsed, control_dt)
        if not self.running:
            return list(self.duties)
        if self.state == "SETTLE":
            if state_at_start == "SETTLE":
                self._phase_elapsed += elapsed
                for channel in self.participating:
                    self._within[channel] = (self._within[channel] + control_dt
                                             if self._near[channel] else 0.0)
            if self._phase_elapsed >= self._settings["reach_timeout_s"]:
                # Each survivor keeps its own settling history. A lagging
                # peer cannot turn already-settled channels into faults.
                for channel in self.participating:
                    if self._within[channel] + 1e-9 < self._settings["settle_s"]:
                        self.fault_channel(channel, "Timed out reaching target")
            if not self.running:
                return list(self.duties)
            if all(self._within[i] + 1e-9 >= self._settings["settle_s"]
                   for i in self.participating):
                self.state = "DWELL"
                self._phase_elapsed = 0.0
                self.message = "Holding target"
                if step["dwell_s"] == 0:
                    self._advance()
        elif self.state == "DWELL":
            for channel in self.participating:
                if self._near[channel]:
                    self._within[channel] += control_dt
                else:
                    self._within[channel] = 0.0
                if self._within[channel] + 1e-9 >= self._settings["settle_s"]:
                    self._outside[channel] = 0.0
                elif self._outside[channel] or not self._near[channel]:
                    # Brief good readings do not continually restart the
                    # deadline for a channel that never recovers stability.
                    self._outside[channel] += elapsed
                    if self._outside[channel] >= self._settings["reach_timeout_s"]:
                        self.fault_channel(channel, "Timed out recovering target during dwell")
            if not self.running:
                return list(self.duties)
            within = all(self._near[i] for i in self.participating)
            if within:
                self._dwell += control_dt
                self.message = "Holding target"
                if self._dwell + 1e-9 >= step["dwell_s"]:
                    self._advance()
            else:
                self.message = "Dwell paused: waiting for all fans"
        return list(self.duties)

    def snapshot(self):
        remaining = 0.0
        if self._profile:
            step = self._profile["steps"][self._index]
            if self.state == "RAMP":
                remaining = max(0.0, step["slew_s"] - self._phase_elapsed)
            elif self.state == "SETTLE":
                remaining = max(0.0, self._settings["reach_timeout_s"] - self._phase_elapsed)
            elif self.state == "DWELL":
                remaining = max(0.0, step["dwell_s"] - self._dwell)
        fans = []
        for channel in range(6):
            reading = self._readings[channel]
            valid = reading.get("valid", False)
            rpm = reading.get("rpm") if valid else None
            participating = channel in self.participating
            target = self.target_rpm if participating else 0
            fans.append({"enabled": channel in self.enabled, "rpm": rpm,
                         "valid": valid, "duty": self.duties[channel],
                         "participating": participating, "fault": self.faults[channel],
                         "target_rpm": target,
                         "error": target - rpm if valid else None})
        return {
            "state": self.state, "running": self.running,
            "target_rpm": self.target_rpm,
            "step": self._index + 1 if self._profile else 0,
            "step_count": len(self._profile["steps"]) if self._profile else 0,
            "phase_remaining_s": remaining,
            "recipe_name": self._profile["name"] if self._profile else "",
            "message": self.message, "elapsed_s": self._elapsed,
            "standstill_assumed": self._standstill_assumed,
            "coasting_without_tach": self._blind_coast,
            "fault_count": sum(fault is not None for fault in self.faults),
            "fans": fans,
        }
