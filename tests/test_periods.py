"""Host timing tests for the PIO period accumulator, without hardware access."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class PeriodTests(unittest.TestCase):
    PERIOD = 1 << 30

    def setUp(self):
        self.now = 0
        machine = types.ModuleType("machine")
        machine.disable_irq = lambda: 7
        machine.enable_irq = lambda state: self.assertEqual(state, 7)
        clock = types.ModuleType("time")
        clock.ticks_us = lambda: self.now % self.PERIOD
        clock.ticks_diff = lambda a, b: (
            (a - b + self.PERIOD // 2) % self.PERIOD - self.PERIOD // 2)
        path = Path(__file__).resolve().parents[1] / "device" / "periods.py"
        spec = importlib.util.spec_from_file_location("periods_under_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"machine": machine, "time": clock}):
            spec.loader.exec_module(module)
        self.Measurements = module.PeriodMeasurements
        self.m = self.Measurements()

    def record(self, period_us, gap_us=None):
        self.now += period_us if gap_us is None else gap_us
        self.m.record(period_us)

    def test_startup_requires_two_complete_periods(self):
        reading = self.m.sample()
        self.assertFalse(reading["valid"])
        self.assertIsNone(reading["age_ms"])
        self.assertEqual(reading["samples"], 0)
        self.record(10000)
        self.assertFalse(self.m.sample()["valid"])
        self.record(10000)
        reading = self.m.sample()
        self.assertTrue(reading["valid"])
        self.assertEqual(reading["rpm"], 3000)
        self.assertEqual(reading["pulses"], 2)

    def test_exact_speeds_without_rated_speed_cap(self):
        for period, rpm in ((10000, 3000), (7500, 4000), (5000, 6000)):
            with self.subTest(rpm=rpm):
                self.m.record(0)
                self.record(period)
                self.record(period)
                reading = self.m.sample()
                self.assertEqual(reading["rpm"], rpm)
                self.assertEqual(reading["peak_rpm"], rpm)

    def test_rolling_window_retains_latest_eight(self):
        for period in (5000,) * 8:
            self.record(period)
        self.assertEqual(self.m.sample()["rpm"], 6000)
        self.record(13000)
        reading = self.m.sample()
        self.assertEqual(reading["samples"], 8)
        self.assertEqual(reading["period_us"], 6000)
        self.assertEqual(reading["rpm"], 5000)
        self.assertEqual(reading["pulses"], 9)
        self.assertEqual(reading["peak_rpm"], 6000)

    def test_mean_period_not_mean_of_instantaneous_rpm(self):
        self.record(5000)
        self.record(15000)
        reading = self.m.sample()
        self.assertEqual(reading["period_us"], 10000)
        self.assertEqual(reading["rpm"], 3000)
        self.assertNotEqual(reading["rpm"], (6000 + 2000) / 2)

    def test_identical_periods_refresh_timestamp(self):
        for _ in range(4):
            self.record(10000, gap_us=1000000)
            self.m.sample()
        self.now += 2000
        reading = self.m.sample()
        self.assertTrue(reading["valid"])
        self.assertEqual(reading["age_ms"], 2)
        self.assertEqual(reading["pulses"], 4)

    def test_timeout_and_restart_discard_stale_first_period(self):
        self.record(10000)
        self.record(10000)
        self.assertEqual(self.m.sample()["rpm"], 3000)
        self.now += 1500000
        reading = self.m.sample()
        self.assertFalse(reading["valid"])
        self.assertEqual(reading["rpm"], 0)
        self.assertEqual(reading["samples"], 0)
        self.record(10000)
        self.assertEqual(self.m.sample()["samples"], 0)
        self.record(20000)
        self.assertFalse(self.m.sample()["valid"])
        self.record(20000)
        reading = self.m.sample()
        self.assertEqual(reading["rpm"], 1500)
        self.assertEqual(reading["pulses"], 4)
        self.assertEqual(reading["peak_rpm"], 3000)

    def test_wall_gap_discards_first_even_without_timeout_sample(self):
        self.record(10000)
        self.record(10000)
        self.record(10000, gap_us=1500000)
        self.assertEqual(self.m.sample()["samples"], 0)
        self.record(10000)
        self.record(10000)
        self.assertTrue(self.m.sample()["valid"])

    def test_sentinel_invalidates_and_accepts_new_complete_periods(self):
        self.m.record(0)
        self.record(10000)
        self.record(10000)
        self.assertTrue(self.m.sample()["valid"])
        self.m.record(0)
        reading = self.m.sample()
        self.assertFalse(reading["valid"])
        self.assertEqual(reading["samples"], 0)
        self.record(7500)
        self.record(7500)
        self.assertEqual(self.m.sample()["rpm"], 4000)

    def test_clock_wrap_and_latched_timeout(self):
        self.now = self.PERIOD - 15000
        self.record(10000)
        self.record(10000)
        self.assertEqual(self.m.sample()["rpm"], 3000)
        self.now += 1500000
        self.assertFalse(self.m.sample()["valid"])
        self.now += self.PERIOD - 1500000
        self.assertFalse(self.m.sample()["valid"])
        self.record(10000)
        self.record(10000)
        self.record(10000)
        self.assertEqual(self.m.sample()["rpm"], 3000)

    def test_invalid_durations_and_irq_sum_bound(self):
        self.record(10000)
        self.m.record(1500001)
        self.assertEqual(self.m.sample()["samples"], 0)
        self.m.record(-1)
        self.assertFalse(self.m.sample()["valid"])
        with self.assertRaises(ValueError):
            self.Measurements(window=400, timeout_ms=1500)


if __name__ == "__main__":
    unittest.main()
