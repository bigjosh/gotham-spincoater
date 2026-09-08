"""Run on a host with: python -m unittest discover -s tests -v

These tests model tach edge timing and electrical PWM commands; they do not
substitute for checking the real wiring, supply, signal levels, or fan response.
"""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class Clock:
    PERIOD = 1 << 30

    def __init__(self):
        self.now = 0

    def ticks_us(self):
        return self.now % self.PERIOD

    def advance(self, us):
        self.now += us

    def ticks_diff(self, a, b):
        half = self.PERIOD // 2
        return (a - b + half) % self.PERIOD - half


class PinStub:
    OUT, IN, PULL_UP, IRQ_FALLING = 1, 2, 3, 4
    instances = []

    def __init__(self, number, mode, pull=None, *, value=None):
        self.number, self.mode, self.pull, self.level = number, mode, pull, value
        self.handler = None
        self.init_history = [(mode, value)]
        self.instances.append(self)

    def init(self, mode, *, value=None):
        self.mode, self.level = mode, value
        self.init_history.append((mode, value))

    def irq(self, *, handler, trigger=None, hard=None):
        self.handler, self.trigger, self.hard = handler, trigger, hard

    def falling_edge(self):
        if self.handler:
            self.handler(self)


class PWMStub:
    instances = []
    slice_frequencies = {}

    def __init__(self, pin, *, freq=None, duty_u16):
        self.slice = (pin.number >> 1) & 7
        self.frequency_was_set = freq is not None
        if freq is not None:
            self.slice_frequencies[self.slice] = freq
        self.pin, self.frequency, self.duty = (
            pin, self.slice_frequencies[self.slice], duty_u16)
        self.initial_pin_level = pin.level
        self.dead = False
        self.instances.append(self)

    def duty_u16(self, value=None):
        if value is not None:
            self.duty = value
        return self.duty

    def freq(self):
        return self.frequency

    def deinit(self):
        self.level_at_deinit = self.pin.level
        self.mode_at_deinit = self.pin.mode
        self.duty_at_deinit = self.duty
        # Real hardware stops both channels, not just this Python object.
        for channel in self.instances:
            if channel.slice == self.slice:
                channel.dead = True


class OpenDrainPWMStub:
    """Only verifies Fan's backend calls, not the real PIO waveform."""

    def __init__(self, *, pin, sm_id, freq):
        self.pin = next(p for p in reversed(PinStub.instances) if p.number == pin)
        self.sm_id, self.frequency = sm_id, freq
        self.initial_pin_level = self.pin.level
        self.duty, self.electrical_mode, self.dead = 0, "low", False

    def set_duty(self, percent):
        self.duty = percent
        self.electrical_mode = "low" if percent == 0 else (
            "release" if percent == 100 else "low/release PWM")

    def close(self):
        self.set_duty(0)
        self.pin.init(PinStub.OUT, value=0)
        self.level_at_deinit, self.mode_at_deinit = self.pin.level, self.pin.mode
        self.dead = True


class FanTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        PinStub.instances = []
        PWMStub.instances = []
        PWMStub.slice_frequencies = {}
        machine = types.ModuleType("machine")
        machine.Pin, machine.PWM = PinStub, PWMStub
        machine.disable_irq = lambda: 37
        machine.enable_irq = lambda state: self.assertEqual(state, 37)
        fake_time = types.ModuleType("time")
        fake_time.ticks_us = self.clock.ticks_us
        fake_time.ticks_diff = self.clock.ticks_diff
        fake_pwm = types.ModuleType("open_drain_pwm")
        fake_pwm.OpenDrainPWM = OpenDrainPWMStub
        path = Path(__file__).resolve().parents[1] / "device" / "fan.py"
        spec = importlib.util.spec_from_file_location("fan_under_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"machine": machine, "time": fake_time,
                                     "open_drain_pwm": fake_pwm}):
            spec.loader.exec_module(module)
        self.Fan = module.Fan
        self.fan = self.Fan()

    def edge(self, delta_us=10000, fan=None):
        self.clock.advance(delta_us)
        (fan or self.fan)._tach.falling_edge()

    def test_boot_stop_and_no_pulses(self):
        self.assertEqual(self.fan._pwm_pin.number, 18)
        self.assertEqual(self.fan._tach.number, 19)
        self.assertEqual(self.fan._pwm.initial_pin_level, 0)
        self.assertEqual(self.fan._pwm.duty, 0)
        self.assertEqual(self.fan._pwm.frequency, 25000)
        self.assertEqual(self.fan._pwm.sm_id, 0)
        self.assertEqual(self.fan._pwm.electrical_mode, "low")
        self.assertEqual(self.fan._tach.pull, PinStub.PULL_UP)
        self.assertTrue(self.fan._tach.hard)
        reading = self.fan.sample()
        self.assertFalse(reading["valid"])
        self.assertIsNone(reading["age_ms"])
        self.assertEqual(reading["rpm"], 0)
        self.assertEqual(reading["peak_rpm"], 0)
        self.clock.advance(2000000)
        self.assertFalse(self.fan.sample()["valid"])

    def test_two_intervals_required_for_startup(self):
        self.edge()
        self.assertFalse(self.fan.sample()["valid"])
        self.edge()
        reading = self.fan.sample()
        self.assertFalse(reading["valid"])
        self.assertEqual(reading["peak_rpm"], 0)
        self.edge()
        reading = self.fan.sample()
        self.assertTrue(reading["valid"])
        self.assertEqual(reading["rpm"], 3000)
        self.assertEqual(reading["pulses"], 3)

    def test_exact_3000_rpm_and_display_phase_independence(self):
        for _ in range(51):
            self.edge()
        self.clock.advance(1234)
        reading = self.fan.sample()
        self.assertEqual(reading["rpm"], 3000)
        self.assertEqual(reading["hz"], 100)
        self.assertEqual(reading["age_ms"], 1)
        self.assertEqual(reading["peak_rpm"], 3000)
        self.edge(8766)
        self.edge()
        self.clock.advance(6789)
        reading = self.fan.sample()
        self.assertEqual(reading["rpm"], 3000)
        self.assertEqual(reading["pulses"], 53)

    def test_average_uses_all_complete_intervals(self):
        self.edge()
        self.edge(8000)
        self.edge(12000)
        self.assertEqual(self.fan.sample()["rpm"], 3000)
        self.edge(7000)
        self.edge(9000)
        self.edge(14000)
        self.clock.advance(2345)
        self.assertEqual(self.fan.sample()["rpm"], 3000)

    def test_stop_timeout_and_fresh_restart(self):
        for _ in range(3):
            self.edge()
        self.assertTrue(self.fan.sample()["valid"])
        self.clock.advance(1499999)
        self.assertTrue(self.fan.sample()["valid"])
        self.clock.advance(1)
        reading = self.fan.sample()
        self.assertFalse(reading["valid"])
        self.assertEqual(reading["rpm"], 0)
        self.assertEqual(reading["hz"], 0)
        self.assertEqual(reading["age_ms"], 1500)
        self.assertEqual(reading["peak_rpm"], 3000)
        self.edge()
        self.assertFalse(self.fan.sample()["valid"])
        self.edge(20000)
        self.assertFalse(self.fan.sample()["valid"])
        self.edge(20000)
        reading = self.fan.sample()
        self.assertEqual(reading["rpm"], 1500)
        self.assertEqual(reading["pulses"], 6)
        self.assertEqual(reading["peak_rpm"], 3000)

    def test_bounce_is_not_counted_or_used_as_last_timestamp(self):
        self.edge()
        self.edge(100)
        self.edge(200)
        self.edge(9700)
        self.edge(499)
        self.edge(9501)
        reading = self.fan.sample()
        self.assertEqual(reading["pulses"], 3)
        self.assertEqual(reading["rpm"], 3000)

    def test_configurable_bounce_filter_and_no_rated_speed_cap(self):
        self.fan.close()
        fan = self.Fan(min_edge_us=100)
        for _ in range(3):
            self.edge(4000, fan)
        reading = fan.sample()
        self.assertEqual(reading["rpm"], 7500)
        self.assertEqual(reading["peak_rpm"], 7500)

    def test_ticks_wrap_during_rotation_and_timeout(self):
        self.clock.now = self.clock.PERIOD - 25000
        for _ in range(3):
            self.edge()
        reading = self.fan.sample()
        self.assertTrue(reading["valid"])
        self.assertEqual(reading["rpm"], 3000)
        self.clock.advance(1500000)
        self.assertFalse(self.fan.sample()["valid"])

    def test_counter_wrap_extends_total_outside_irq(self):
        self.fan._edge_count = self.fan._COUNT_MASK - 1
        self.fan._accounted_count = self.fan._edge_count
        for _ in range(3):
            self.edge()
        reading = self.fan.sample()
        self.assertEqual(reading["pulses"], 3)
        self.assertEqual(reading["rpm"], 3000)

    def test_long_idle_does_not_revive_stale_rpm_after_clock_wrap(self):
        for _ in range(3):
            self.edge()
        self.assertTrue(self.fan.sample()["valid"])
        self.clock.advance(1500000)
        self.assertFalse(self.fan.sample()["valid"])
        self.clock.advance(self.clock.PERIOD - 1500000)
        self.assertFalse(self.fan.sample()["valid"])
        self.edge()
        self.assertFalse(self.fan.sample()["valid"])
        self.edge()
        self.edge()
        self.assertEqual(self.fan.sample()["rpm"], 3000)

    def test_duty_polarities_clamps_and_close(self):
        self.fan.close()
        for inverted in (False, True):
            with self.subTest(inverted=inverted):
                fan = self.Fan(inverted=inverted)
                stopped = 65535 if inverted else 0
                running = 0 if inverted else 100
                self.assertEqual(fan._pwm.duty, stopped)
                self.assertEqual(fan._pwm.initial_pin_level, int(inverted))
                self.assertEqual(fan.set_duty(-20), 0)
                self.assertEqual(fan._pwm.duty, stopped)
                self.assertEqual(fan.set_duty(120), 100)
                self.assertEqual(fan._pwm.duty, running)
                if not inverted:
                    self.assertEqual(fan._pwm.electrical_mode, "release")
                self.assertEqual(fan.sample()["duty"], 100)
                fan.set_duty(50)
                self.assertEqual(fan._pwm.duty, 32768 if inverted else 50)
                fan.close()
                self.assertTrue(fan._pwm.dead)
                self.assertEqual(fan._pwm.level_at_deinit, int(inverted))
                self.assertEqual(fan._pwm.mode_at_deinit, PinStub.OUT)
                if not inverted:
                    self.assertEqual(fan._pwm.electrical_mode, "low")
                self.assertIsNone(fan._tach.handler)
                self.assertEqual(fan.sample()["duty"], 0)
                fan.close()  # Idempotent shutdown.
                with self.assertRaises(RuntimeError):
                    fan.set_duty(30)

    def test_push_pull_10khz_starts_stopped_and_keeps_tach_pullup(self):
        self.fan.close()
        fan = self.Fan(push_pull=True, pwm_hz=10000)
        self.assertIsInstance(fan._pwm, PWMStub)
        self.assertEqual(fan._pwm_pin.number, 18)
        self.assertEqual(fan._pwm.initial_pin_level, 0)
        self.assertEqual(fan._pwm.duty, 0)
        self.assertEqual(fan._pwm.frequency, 10000)
        self.assertEqual(fan._tach.number, 19)
        self.assertEqual(fan._tach.mode, PinStub.IN)
        self.assertEqual(fan._tach.pull, PinStub.PULL_UP)
        self.assertEqual(fan._tach.trigger, PinStub.IRQ_FALLING)
        self.assertTrue(fan._tach.hard)
        for _ in range(3):
            self.edge(fan=fan)
        self.assertEqual(fan.sample()["rpm"], 3000)

    def test_push_pull_duty_is_high_positive_and_clamped(self):
        self.fan.close()
        fan = self.Fan(push_pull=True, pwm_hz=10000)
        for requested, applied, high_ticks in (
                (0, 0, 0), (25, 25, 16384), (50, 50, 32768),
                (100, 100, 65535), (-20, 0, 0), (120, 100, 65535)):
            with self.subTest(requested=requested):
                self.assertEqual(fan.set_duty(requested), applied)
                self.assertEqual(fan._pwm.duty, high_ticks)
                self.assertEqual(fan.sample()["duty"], applied)
        with self.assertRaises(ValueError):
            fan.set_duty(float("nan"))
        self.assertEqual(fan._pwm.duty, 65535)

    def test_push_pull_close_commands_zero_and_holds_low(self):
        self.fan.close()
        fan = self.Fan(push_pull=True, pwm_hz=10000)
        fan.set_duty(100)
        fan.close()
        self.assertTrue(fan._pwm.dead)
        self.assertEqual(fan._pwm.duty_at_deinit, 0)
        self.assertEqual(fan._pwm.level_at_deinit, 0)
        self.assertEqual(fan._pwm.mode_at_deinit, PinStub.OUT)
        self.assertEqual(fan._pwm_pin.level, 0)
        self.assertIsNone(fan._tach.handler)
        self.assertEqual(fan.sample()["duty"], 0)
        fan.close()
        with self.assertRaises(RuntimeError):
            fan.set_duty(30)

    def test_pwm_frequency_must_be_positive(self):
        for frequency in (0, -1, -10000):
            with self.subTest(frequency=frequency):
                with self.assertRaises(ValueError):
                    self.Fan(push_pull=True, pwm_hz=frequency)

    def test_invalid_constructor_does_not_touch_gpio(self):
        initial_pins = list(PinStub.instances)
        for kwargs in (
                {'pwm_pin': 26}, {'pwm_pin': True}, {'tach_pin': 25},
                {'pwm_pin': 0, 'tach_pin': 0}, {'pulses_per_rev': 0},
                {'min_edge_us': 0}, {'timeout_ms': 0}, {'pwm_hz': 10000.0},
                {'sm_id': 12}, {'synchronous': True},
                {'synchronous': True, 'push_pull': True, 'pwm_hz': 10000,
                 'inverted': True},
                {'synchronous': True, 'push_pull': True, 'pwm_hz': 10000,
                 'tach_sm_id': 12},
                {'synchronous': True, 'push_pull': True, 'pwm_hz': 10000,
                 'period_window': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.Fan(**kwargs)
            self.assertEqual(PinStub.instances, initial_pins)
        self.assertEqual(self.fan._pwm_pin.init_history, [(PinStub.OUT, 0)])

    def test_gpio_claim_prevents_reusing_pwm_or_tach(self):
        initial_pins = list(PinStub.instances)
        for pwm_pin, tach_pin in ((18, 20), (20, 19), (19, 20), (20, 18)):
            with self.subTest(pins=(pwm_pin, tach_pin)), self.assertRaises(ValueError):
                self.Fan(pwm_pin=pwm_pin, tach_pin=tach_pin, push_pull=True)
            self.assertEqual(PinStub.instances, initial_pins)
        self.fan.close()
        replacement = self.Fan()
        self.assertFalse(replacement._closed)
        replacement.close()

    def test_shared_slice_preserves_sibling_until_last_close(self):
        first = self.Fan(pwm_pin=12, tach_pin=16, push_pull=True, pwm_hz=10000)
        first.set_duty(70)
        second = self.Fan(pwm_pin=13, tach_pin=17, push_pull=True, pwm_hz=10000)
        second.set_duty(40)
        self.assertTrue(first._pwm.frequency_was_set)
        self.assertFalse(second._pwm.frequency_was_set)
        first.close()
        self.assertEqual(first._pwm_pin.level, 0)
        self.assertFalse(second._pwm.dead)
        self.assertEqual(second._pwm.duty, 26214)
        second.set_duty(80)
        self.assertEqual(second._pwm.duty, 52428)
        first.close()  # A second close must not release the sibling's claim.
        self.assertFalse(second._pwm.dead)
        second.close()
        self.assertTrue(second._pwm.dead)
        self.assertNotIn(6, self.Fan._pwm_slices)

    def test_shared_slice_rejects_different_frequency_before_gpio_change(self):
        first = self.Fan(pwm_pin=12, tach_pin=16, push_pull=True, pwm_hz=10000)
        first.set_duty(60)
        initial_pins = list(PinStub.instances)
        with self.assertRaises(ValueError):
            self.Fan(pwm_pin=13, tach_pin=17, push_pull=True, pwm_hz=25000)
        self.assertEqual(PinStub.instances, initial_pins)
        self.assertEqual(first._pwm.duty, 39321)
        self.assertFalse(first._pwm.dead)
        first.close()

    def test_hardware_channel_alias_is_rejected(self):
        first = self.Fan(pwm_pin=0, tach_pin=1, push_pull=True)
        initial_pins = list(PinStub.instances)
        with self.assertRaises(ValueError):
            self.Fan(pwm_pin=16, tach_pin=17, push_pull=True)
        self.assertEqual(PinStub.instances, initial_pins)
        first.close()

    def test_sync_constructor_failure_stops_and_releases_only_its_channel(self):
        first = self.Fan(pwm_pin=12, tach_pin=16, push_pull=True, pwm_hz=10000)
        first.set_duty(60)
        fake_sync = types.ModuleType('sync_tach')

        def fail_sampler(*args, **kwargs):
            raise RuntimeError('sampler allocation failed')

        fake_sync.SyncTachometer = fail_sampler
        with patch.dict(sys.modules, {'sync_tach': fake_sync}):
            with self.assertRaisesRegex(RuntimeError, 'sampler allocation failed'):
                self.Fan(pwm_pin=13, tach_pin=17, push_pull=True, pwm_hz=10000,
                         synchronous=True)
        failed_output = PWMStub.instances[-1]
        self.assertEqual(failed_output.pin.level, 0)
        self.assertEqual(failed_output.duty, 0)
        self.assertFalse(first._pwm.dead)
        self.assertEqual(first._pwm.duty, 39321)
        self.assertNotIn(13, self.Fan._claimed_pins)
        self.assertNotIn(17, self.Fan._claimed_pins)
        replacement = self.Fan(pwm_pin=13, tach_pin=17, push_pull=True, pwm_hz=10000)
        replacement.close()
        self.assertFalse(first._pwm.dead)
        first.close()

    def test_sync_failure_on_only_channel_deinitializes_slice(self):
        self.fan.close()
        fake_sync = types.ModuleType('sync_tach')

        for error in (RuntimeError, KeyboardInterrupt):
            def fail_sampler(*args, **kwargs):
                raise error('sampler allocation failed or interrupted')

            fake_sync.SyncTachometer = fail_sampler
            with self.subTest(error=error), patch.dict(sys.modules, {'sync_tach': fake_sync}):
                with self.assertRaises(error):
                    self.Fan(push_pull=True, pwm_hz=10000, synchronous=True)
            self.assertTrue(PWMStub.instances[-1].dead)
            self.assertEqual(PWMStub.instances[-1].pin.level, 0)
            self.assertFalse(self.Fan._claimed_pins)
            self.assertFalse(self.Fan._claimed_pwm_channels)
            self.assertFalse(self.Fan._pwm_slices)


if __name__ == "__main__":
    unittest.main()
