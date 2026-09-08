"""Selectable PWM and tachometer for a Pico 2 W / four-wire fan bench test.

The default is a DIRECT open-drain PWM connection: low means a 0% fan command,
and released input means 100%. Use inverted=True if an external NPN/NMOS stage
pulls down the fan PWM input (this alternative uses hardware push-pull PWM).
Use push_pull=True for direct, noninverted 0/3.3 V hardware PWM. The application
selects its diagnostic mode and frequency explicitly in config.py.
Direct wiring is specific to the powered RP2350 digital GPIO electrical limits;
do not assume it is safe for an original Pico or an unpowered Pico 2 W.

Tach is assumed open collector, with two falling edges per revolution. The
internal 3.3 V pull-up is enabled for the bench test. An external 4.7 kOhm pull-up
to 3.3 V gives a stronger signal for longer wiring. Never pull tach up to 12 V.
"""

from machine import Pin, PWM, disable_irq, enable_irq
from time import ticks_diff, ticks_us
from open_drain_pwm import OpenDrainPWM


class Fan:
    """Set a duty command and measure RPM; this does not regulate RPM.

    sample() reports valid only after at least two tach intervals (three edges).
    Measurements average complete intervals, without quantizing RPM into pulse
    counts per display frame. Between updates the last result is retained until
    timeout. The peak includes valid averages only, and has no rated-RPM cap.

    The IRQ uses only bounded small integers. Total pulse accounting is extended
    outside the IRQ in sample(); call sample at least once per 2**29 tach edges.
    Timestamp differences must be shorter than half the platform's ticks period.
    """

    _COUNT_MASK = 0x1FFFFFFF
    # Hardware channels A/B share a counter and frequency. Claims cover both
    # signal pins; hardware-channel claims also catch aliases such as GP0/GP16.
    _claimed_pins = set()
    _claimed_pwm_channels = set()
    _pwm_slices = {}

    def __init__(self, pwm_pin=18, tach_pin=19, pulses_per_rev=2,
                 inverted=False, min_edge_us=500, timeout_ms=1500, sm_id=0,
                 push_pull=False, pwm_hz=25000, synchronous=False,
                 tach_sm_id=0, period_window=8):
        # Reject conflicting/invalid requests before changing any existing GPIO.
        if type(pwm_pin) is not int or not 0 <= pwm_pin <= 22:
            raise ValueError('PWM must use a digital Pico header GPIO (0..22)')
        if (type(tach_pin) is not int or
                not (0 <= tach_pin <= 22 or 26 <= tach_pin <= 28)):
            raise ValueError('Tach must use a Pico header GPIO')
        if pwm_pin == tach_pin:
            raise ValueError('PWM and tach must use different GPIOs')
        if synchronous and inverted:
            raise ValueError('Synchronous tach does not support inverted output')
        hardware_pwm = bool(push_pull or inverted)
        if synchronous and (not hardware_pwm or pwm_hz != 10000):
            raise ValueError('Synchronous tach requires hardware PWM at 10 kHz')
        if type(pwm_hz) is not int or pwm_hz <= 0:
            raise ValueError("PWM frequency must be a positive integer")
        if type(pulses_per_rev) is not int or pulses_per_rev <= 0:
            raise ValueError("pulses_per_rev must be a positive integer")
        if (type(min_edge_us) is not int or min_edge_us < 1 or
                type(timeout_ms) is not int or timeout_ms < 1):
            raise ValueError("edge filter and timeout must be positive integers")
        if not hardware_pwm and (type(sm_id) is not int or not 0 <= sm_id < 12):
            raise ValueError('PWM state machine must be 0..11')
        if synchronous:
            if type(tach_sm_id) is not int or not 0 <= tach_sm_id < 12:
                raise ValueError('Tach state machine must be 0..11')
            if type(period_window) is not int or period_window < 2:
                raise ValueError('Period averaging window must be at least two')
            if timeout_ms * 1000 * period_window > self._COUNT_MASK:
                raise ValueError('Period averaging exceeds IRQ small-integer limit')
        if pwm_pin in self._claimed_pins or tach_pin in self._claimed_pins:
            raise ValueError('GPIO already used by another fan')
        pwm_slice = (pwm_pin >> 1) & 7 if hardware_pwm else None
        pwm_channel = pwm_pin & 15 if hardware_pwm else None
        slice_state = self._pwm_slices.get(pwm_slice)
        if hardware_pwm:
            if pwm_channel in self._claimed_pwm_channels:
                raise ValueError('Hardware PWM channel already used by another fan')
            if slice_state is not None and slice_state[0] != pwm_hz:
                raise ValueError('Fans sharing a PWM slice must use the same frequency')

        self._stop_level = 1 if inverted else 0
        self._inverted = bool(inverted)
        self._hardware_pwm = hardware_pwm
        self._period_source = None
        self._pwm = None
        self._pwm_pin = None
        self._tach = None
        self._gpio_numbers = (pwm_pin, tach_pin)
        self._pwm_slice = pwm_slice
        self._pwm_channel = pwm_channel
        self._pins_claimed = False
        self._slice_claimed = False
        self._closed = False
        self.pwm_hz = pwm_hz
        self._ppr = pulses_per_rev
        self._min_edge_us = min_edge_us
        self._timeout_us = timeout_ms * 1000
        self._duty = 0.0

        # Fields read/written in the hard IRQ. No list, float, or big-int work.
        self._seen_edge = False
        self._restart_needed = True
        self._last_us = 0
        self._edge_count = 0
        self._run_first_us = 0
        self._run_first_count = 0

        # Main-loop-only measurement and extended pulse count state.
        self._accounted_count = 0
        self._total_pulses = 0
        self._anchor_us = 0
        self._anchor_count = 0
        self._anchor_run = None
        self._hz = 0.0
        self._peak_rpm = 0.0
        self._claimed_pins.update(self._gpio_numbers)
        self._pins_claimed = True
        if hardware_pwm:
            self._claimed_pwm_channels.add(pwm_channel)
            self._pwm_slices[pwm_slice] = (pwm_hz, 1 if slice_state is None
                                          else slice_state[1] + 1)
            self._slice_claimed = True
        try:
            # Establish the stopped level before selecting the PWM peripheral.
            self._pwm_pin = Pin(pwm_pin, Pin.OUT, value=self._stop_level)
            if hardware_pwm:
                stopped_duty = 65535 if self._inverted else 0
                if slice_state is None:
                    self._pwm = PWM(self._pwm_pin, freq=pwm_hz,
                                    duty_u16=stopped_duty)
                else:
                    # Do not reconfigure the counter of an already running fan.
                    self._pwm = PWM(self._pwm_pin, duty_u16=stopped_duty)
            else:
                self._pwm = OpenDrainPWM(pin=pwm_pin, sm_id=sm_id, freq=pwm_hz)
            self._tach = Pin(tach_pin, Pin.IN, Pin.PULL_UP)
            if synchronous:
                from sync_tach import SyncTachometer
                self._period_source = SyncTachometer(
                    self._pwm, pwm_pin, self._tach, sm_id=tach_sm_id,
                    window=period_window, timeout_ms=timeout_ms,
                    pulses_per_rev=pulses_per_rev)
            else:
                self._tach.irq(handler=self._on_edge, trigger=Pin.IRQ_FALLING, hard=True)
        except BaseException:
            # A failed/interrupted sampler must leave its output stopped and
            # release ownership, without disabling a sibling PWM channel.
            self.close()
            raise

    def _on_edge(self, pin):
        now = ticks_us()
        if self._seen_edge and not self._restart_needed:
            elapsed = ticks_diff(now, self._last_us)
            if elapsed < 0:
                # A long idle may exceed half of the microsecond ticks range.
                elapsed = self._timeout_us
            elif elapsed < self._min_edge_us:
                return
        else:
            elapsed = self._timeout_us

        count = (self._edge_count + 1) & self._COUNT_MASK
        if elapsed >= self._timeout_us:
            # A new run must earn a fresh multi-interval measurement.
            self._run_first_us = now
            self._run_first_count = count
        self._edge_count = count
        self._last_us = now
        self._seen_edge = True
        self._restart_needed = False

    def set_duty(self, percent):
        """Clamp a fan command to 0..100%; return the applied percentage."""
        if self._closed:
            raise RuntimeError("fan is closed")
        percent = float(percent)
        if percent != percent:
            raise ValueError("duty cannot be NaN")
        if percent < 0:
            percent = 0.0
        elif percent > 100:
            percent = 100.0
        if self._hardware_pwm:
            high_percent = 100.0 - percent if self._inverted else percent
            duty = int(high_percent * 65535.0 / 100.0 + 0.5)
            if self._period_source is not None:
                self._period_source.set_duty_u16(duty)
            else:
                self._pwm.duty_u16(duty)
        else:
            self._pwm.set_duty(percent)
        self._duty = percent
        return percent

    def sample(self):
        """Return rpm, hz, total pulses, edge age_ms, valid, peak_rpm, duty.

        hz is tach pulse frequency, not shaft rotations per second. age_ms is
        None before the first accepted edge. pulses excludes filtered bounce.
        """
        if self._period_source is not None:
            reading = self._period_source.sample()
            reading['duty'] = self._duty
            return reading
        irq_state = disable_irq()
        seen = self._seen_edge
        restart_needed = self._restart_needed
        last_us = self._last_us
        count = self._edge_count
        first_us = self._run_first_us
        first_count = self._run_first_count
        enable_irq(irq_state)
        now = ticks_us()

        self._total_pulses += (count - self._accounted_count) & self._COUNT_MASK
        self._accounted_count = count
        age_us = ticks_diff(now, last_us) if seen else None
        fresh = seen and not restart_needed and 0 <= age_us < self._timeout_us

        if not fresh:
            self._hz = 0.0
            self._anchor_run = None
            # Latch timeout so old timestamps cannot appear fresh after a full
            # ticks wrap. The next edge starts a new measurement window.
            irq_state = disable_irq()
            if self._edge_count == count:
                self._restart_needed = True
            enable_irq(irq_state)
        else:
            if self._anchor_run != first_count:
                self._anchor_run = first_count
                self._anchor_us = first_us
                self._anchor_count = first_count
                self._hz = 0.0
            intervals = (count - self._anchor_count) & self._COUNT_MASK
            elapsed_us = ticks_diff(last_us, self._anchor_us)
            if intervals >= 2:
                if elapsed_us > 0:
                    self._hz = intervals * 1000000.0 / elapsed_us
                    rpm = self._hz * 60.0 / self._ppr
                    if rpm > self._peak_rpm:
                        self._peak_rpm = rpm
                else:
                    # Recovery if sampling was suspended beyond ticks' range.
                    self._hz = 0.0
                self._anchor_us = last_us
                self._anchor_count = count

        valid = bool(fresh and self._hz > 0)
        return {
            "rpm": self._hz * 60.0 / self._ppr if valid else 0.0,
            "hz": self._hz if valid else 0.0,
            "pulses": self._total_pulses,
            "age_ms": max(0, age_us // 1000) if seen else None,
            "valid": valid,
            "peak_rpm": self._peak_rpm,
            "duty": self._duty,
        }

    def close(self):
        """Stop PWM and hold the stopped logic level; do not leave it floating.

        This commands 0% but cannot disconnect the fan's 12 V supply. Whether
        the fan stops at 0% is a property to verify with the connected model.
        """
        if self._closed:
            return
        self._closed = True
        self._duty = 0.0
        try:
            # Hold this output stopped even if later sampler cleanup raises.
            if self._pwm_pin is not None:
                self._pwm_pin.init(Pin.OUT, value=self._stop_level)
            if self._pwm is not None:
                if self._hardware_pwm:
                    self._pwm.duty_u16(65535 if self._inverted else 0)
                else:
                    self._pwm.close()
        finally:
            try:
                if self._period_source is not None:
                    self._period_source.close()
            finally:
                self._period_source = None
                try:
                    if self._tach is not None:
                        self._tach.irq(handler=None)
                finally:
                    self._release_claims()

    def _release_claims(self):
        try:
            if self._slice_claimed:
                self._slice_claimed = False
                self._claimed_pwm_channels.discard(self._pwm_channel)
                frequency, users = self._pwm_slices[self._pwm_slice]
                if users > 1:
                    self._pwm_slices[self._pwm_slice] = (frequency, users - 1)
                else:
                    del self._pwm_slices[self._pwm_slice]
                    # deinit() halts both A and B: only the last owner may do it.
                    if self._pwm is not None:
                        self._pwm.deinit()
        finally:
            if self._pins_claimed:
                self._pins_claimed = False
                self._claimed_pins.difference_update(self._gpio_numbers)
