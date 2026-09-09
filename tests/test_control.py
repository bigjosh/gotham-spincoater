import importlib.util
from pathlib import Path
import sys
import unittest


DEVICE = Path(__file__).resolve().parents[1] / "device"
SAVED = sys.path[:]
sys.path.insert(0, str(DEVICE))
try:
    from recipes import DEFAULT_SETTINGS
    SPEC = importlib.util.spec_from_file_location("_control_under_test", DEVICE / "control.py")
    control = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(control)
finally:
    sys.path[:] = SAVED


def profile(slew=0, dwell=1, rpm=3000):
    return {"name": "Test", "steps": [
        {"rpm": 0, "slew_s": 0, "dwell_s": 0},
        {"rpm": rpm, "slew_s": slew, "dwell_s": dwell},
        {"rpm": 0, "slew_s": 0, "dwell_s": 0},
    ]}


def reading(rpm=None, pulses=None, **extra):
    result = {"rpm": rpm if rpm is not None else 0, "valid": rpm is not None}
    if pulses is not None:
        result["pulses"] = pulses
    result.update(extra)
    return result


class EngineTests(unittest.TestCase):
    def make(self, enabled=(0,), recipe=None, **settings):
        engine = control.ControlEngine(enabled)
        chosen = dict(DEFAULT_SETTINGS)
        chosen.update(settings)
        engine.start(recipe or profile(), chosen, 0)
        return engine

    def initial_zero(self, engine):
        for now in range(0, 501, 20):
            engine.update(now, {channel: reading(0) for channel in engine.enabled})
        self.assertEqual(engine.snapshot()["step"], 2)
        return 500

    def test_idle_and_disabled_channels(self):
        engine = control.ControlEngine((0, 5))
        self.assertFalse(engine.running)
        self.assertEqual(engine.update(100, {}), [0.0] * 6)
        self.assertEqual([fan["enabled"] for fan in engine.snapshot()["fans"]],
                         [True, False, False, False, False, True])

    def test_empty_selection_is_idle_but_cannot_start(self):
        engine = control.ControlEngine(())
        self.assertEqual(engine.update(20, {}), [0] * 6)
        with self.assertRaisesRegex(ValueError, 'Enable at least one fan'):
            engine.start(profile(), DEFAULT_SETTINGS, 0)
        self.assertFalse(engine.running)

    def test_run_participants_cannot_change_until_stopped(self):
        engine = self.make(enabled=(0, 2))
        with self.assertRaisesRegex(RuntimeError, 'Stop the recipe'):
            engine.set_enabled((0,))
        self.assertEqual(engine.enabled, (0, 2))
        engine.stop()
        engine.set_enabled((0,))
        self.assertEqual(engine.enabled, (0,))
        self.assertEqual(engine.duties, [0] * 6)
        self.assertFalse(engine.snapshot()['fans'][2]['valid'])

    def test_disabled_missing_tach_and_latches_do_not_delay_or_fault_run(self):
        engine = self.make(enabled=(0,), recipe=profile(dwell=0))
        self.initial_zero(engine)
        ignored = reading(stopped=True, fault='command_underrun', overflow=True)
        for now in range(520, 1021, 20):
            engine.update(now, {0: reading(3000), 1: ignored})
        self.assertEqual(engine.snapshot()['step'], 3)
        for now in range(1040, 1561, 20):
            engine.update(now, {0: reading(0), 1: ignored})
        self.assertEqual(engine.state, 'COMPLETE')
        self.assertEqual(engine.duties, [0] * 6)

    def test_invalid_selection_does_not_replace_previous_selection(self):
        engine = control.ControlEngine((0, 1))
        for invalid in ((0, 0), (True,), (6,), (-1,)):
            with self.assertRaises(ValueError):
                engine.set_enabled(invalid)
            self.assertEqual(engine.enabled, (0, 1))

    def test_independent_seekers_and_configurable_rate_limit(self):
        engine = self.make(enabled=(0, 1), max_power_per_s=8)
        self.initial_zero(engine)
        for now in range(520, 1021, 20):
            duties = engine.update(now, {0: reading(1000), 1: reading(2900)})
        self.assertAlmostEqual(duties[0], 4.16, places=6)
        self.assertAlmostEqual(duties[1], 0.52, places=6)
        self.assertEqual(duties[2:], [0] * 4)

    def test_loop_stall_does_not_create_large_power_jump(self):
        engine = self.make()
        self.initial_zero(engine)
        engine.update(520, {0: reading(1000)})
        before = engine.duties[0]
        engine.update(5520, {0: reading(1000)})
        self.assertAlmostEqual(engine.duties[0] - before, 1.0)

    def test_conservative_error_gain_and_deadband(self):
        engine = self.make()
        self.initial_zero(engine)
        self.assertEqual(engine.SEEK_GAIN, 0.01)
        for now in range(520, 1501, 20):
            engine.update(now, {0: reading(2800)})
        self.assertAlmostEqual(engine.duties[0], 2.0)
        before = engine.duties[0]
        engine.update(1520, {0: reading(2975)})
        self.assertEqual(engine.duties[0], before)

    def test_shared_target_ramps_linearly(self):
        engine = self.make(enabled=(0, 1), recipe=profile(slew=2))
        self.initial_zero(engine)
        engine.update(1500, {0: reading(1000), 1: reading(1200)})
        self.assertEqual(engine.target_rpm, 1500)
        self.assertEqual(engine.snapshot()["phase_remaining_s"], 1)
        self.assertEqual(engine.snapshot()["fans"][0]["target_rpm"], 1500)
        self.assertEqual(engine.snapshot()["fans"][1]["target_rpm"], 1500)

    def test_all_fans_must_settle_before_dwell(self):
        engine = self.make(enabled=(0, 1))
        self.initial_zero(engine)
        for now in range(520, 1521, 20):
            engine.update(now, {0: reading(3000), 1: reading(2900)})
        self.assertEqual(engine.state, "SETTLE")
        for now in range(1540, 2041, 20):
            engine.update(now, {0: reading(3000), 1: reading(3000)})
        self.assertEqual(engine.state, "DWELL")

    def test_dwell_pauses_when_any_fan_leaves_tolerance(self):
        engine = self.make(recipe=profile(dwell=2))
        self.initial_zero(engine)
        for now in range(520, 1021, 20):
            engine.update(now, {0: reading(3000)})
        self.assertEqual(engine.state, "DWELL")
        for now in range(1040, 1541, 20):
            engine.update(now, {0: reading(3000)})
        remaining = engine.snapshot()["phase_remaining_s"]
        for now in range(1560, 2061, 20):
            engine.update(now, {0: reading(2000)})
        self.assertEqual(engine.snapshot()["phase_remaining_s"], remaining)
        self.assertIn("paused", engine.message)

    def test_reach_timeout_faults_every_channel(self):
        engine = self.make(enabled=(0, 1), reach_timeout_s=1)
        self.initial_zero(engine)
        for now in range(520, 1521, 20):
            engine.update(now, {0: reading(1000), 1: reading(3000)})
        self.assertEqual(engine.state, "FAULT")
        self.assertEqual(engine.duties, [0] * 6)

    def test_missing_startup_tach_is_bounded_and_faults(self):
        engine = self.make(max_power_per_s=100)
        self.initial_zero(engine)
        max_duty = 0
        for now in range(520, 8521, 20):
            engine.update(now, {0: reading()})
            max_duty = max(max_duty, engine.duties[0])
        self.assertEqual(max_duty, 30)
        self.assertEqual(engine.state, "FAULT")
        self.assertEqual(engine.duties, [0] * 6)

    def test_startup_has_eight_seconds_to_find_tach_under_power_ceiling(self):
        engine = self.make()
        self.initial_zero(engine)
        for now in range(520, 7521, 20):
            engine.update(now, {0: reading()})
        self.assertTrue(engine.running)
        self.assertEqual(engine.duties[0], 30)
        engine.update(7540, {0: reading(600)})
        self.assertTrue(engine.running)
        self.assertGreater(engine.duties[0], 30)

    def begin_descent(self, endpoint=0, duty=20):
        chosen = profile(dwell=0)
        chosen["steps"][-1]["slew_s"] = 120
        if endpoint:
            chosen["steps"].insert(-1, {"rpm": endpoint, "slew_s": 120, "dwell_s": 0})
        engine = self.make(recipe=chosen)
        self.initial_zero(engine)
        for now in range(520, 1021, 20):
            engine.update(now, {0: reading(3000)})
        self.assertEqual(engine.snapshot()["step"], 3)
        self.assertEqual(engine.state, "RAMP")
        # Seed established power; the test exercises loss after a running fan
        # enters its shutdown deadzone, independent of a particular fan curve.
        engine.duties[0] = duty
        return engine

    def test_missing_tach_during_low_power_zero_descent_only_removes_power(self):
        engine = self.begin_descent()
        previous = engine.duties[0]
        for now in range(1040, 5021, 20):
            engine.update(now, {0: reading()})
            self.assertLessEqual(engine.duties[0], previous)
            self.assertLessEqual(previous - engine.duties[0], 0.200001)
            previous = engine.duties[0]
        self.assertEqual(engine.state, "RAMP")
        self.assertEqual(engine.duties[0], 0)
        self.assertTrue(engine.snapshot()["coasting_without_tach"])
        self.assertFalse(engine.snapshot()["fans"][0]["valid"])

    def test_other_descent_signal_losses_still_fault(self):
        for endpoint, duty in ((1000, 20), (0, 31)):
            engine = self.begin_descent(endpoint=endpoint, duty=duty)
            for now in range(1040, 2621, 20):
                engine.update(now, {0: reading()})
            self.assertEqual(engine.state, "FAULT")
            self.assertEqual(engine.duties, [0] * 6)

    def test_lost_tach_holds_then_faults_without_blind_acceleration(self):
        engine = self.make()
        self.initial_zero(engine)
        engine.update(520, {0: reading(1000)})
        before = engine.duties[0]
        for now in range(540, 1021, 20):
            engine.update(now, {0: reading()})
        self.assertEqual(engine.duties[0], before)
        engine.update(2100, {0: reading()})
        self.assertEqual(engine.state, "FAULT")

    def test_emergency_latch_stops_all_and_does_not_rearm(self):
        engine = self.make(enabled=(0, 1))
        self.initial_zero(engine)
        engine.update(520, {0: reading(1000), 1: reading(1000)})
        engine.update(540, {0: reading(1000), 1: reading(1000, stopped=True)})
        self.assertEqual(engine.state, "STOPPED")
        self.assertEqual(engine.duties, [0] * 6)
        engine.update(560, {0: reading(1000), 1: reading(1000)})
        self.assertEqual(engine.state, "STOPPED")

    def test_fault_and_explicit_stop_reasons_survive_following_pio_latches(self):
        engine = self.make()
        engine._fault("Fan 0: tach signal missing")
        engine.update(20, {0: reading(stopped=True)})
        self.assertEqual(engine.state, "FAULT")
        self.assertEqual(engine.message, "Fan 0: tach signal missing")
        engine.stop("STOP button")
        engine.update(40, {0: reading(stopped=True)})
        self.assertEqual(engine.state, "STOPPED")
        self.assertEqual(engine.message, "STOP button")

    def test_hardware_faults_have_priority_over_emergency_latch(self):
        for fault in ("command_underrun", "command_stall", "clock_changed"):
            engine = self.make(enabled=(0, 1))
            engine.update(20, {0: reading(), 1: reading(stopped=True, fault=fault)})
            self.assertEqual(engine.state, "FAULT")
            self.assertIn(fault, engine.message)
            self.assertIn("Fan 1", engine.message)
            self.assertEqual(engine.duties, [0] * 6)

    def test_zero_endpoint_uses_quiet_without_claiming_measured_zero(self):
        engine = self.make()
        for now in range(0, 2021, 20):
            engine.update(now, {0: reading(pulses=0)})
        self.assertEqual(engine.snapshot()["step"], 2)
        self.assertFalse(engine.snapshot()["fans"][0]["valid"])
        self.assertIsNone(engine.snapshot()["fans"][0]["rpm"])

    def test_coasting_pulses_prevent_assumed_standstill(self):
        engine = self.make()
        for now in range(0, 3021, 20):
            engine.update(now, {0: reading(pulses=now // 100)})
        self.assertEqual(engine.snapshot()["step"], 1)
        self.assertFalse(engine.snapshot()["standstill_assumed"])

    def test_overflow_blocks_initial_zero_settling_despite_unchanged_pulse_count(self):
        engine = self.make()
        for now in range(0, 3021, 20):
            # Exercise the contradictory valid=True too: overflow takes
            # priority over a stale source value and an unchanged counter.
            engine.update(now, {0: reading(0, pulses=0, overflow=True)})
        self.assertEqual(engine.snapshot()["step"], 1)
        self.assertEqual(engine.state, "SETTLE")
        self.assertFalse(engine.snapshot()["standstill_assumed"])
        self.assertFalse(engine.snapshot()["fans"][0]["valid"])

    def test_overflow_blocks_final_zero_settling_until_fresh_quiet_interval(self):
        engine = self.make(recipe=profile(dwell=0))
        self.initial_zero(engine)
        for now in range(520, 1021, 20):
            engine.update(now, {0: reading(3000, pulses=10)})
        self.assertEqual(engine.snapshot()["step"], 3)
        for now in range(1040, 4041, 20):
            engine.update(now, {0: reading(pulses=10, overflow=True)})
        self.assertEqual(engine.state, "SETTLE")
        self.assertFalse(engine.snapshot()["standstill_assumed"])
        for now in range(4060, 5521, 20):
            engine.update(now, {0: reading(pulses=10)})
        self.assertEqual(engine.state, "SETTLE")
        for now in range(5540, 6061, 20):
            engine.update(now, {0: reading(pulses=10)})
        self.assertEqual(engine.state, "COMPLETE")

    def test_complete_and_explicit_stop_remain_off(self):
        engine = self.make(recipe=profile(dwell=0))
        self.initial_zero(engine)
        for now in range(520, 1021, 20):
            engine.update(now, {0: reading(3000)})
        self.assertEqual(engine.snapshot()["step"], 3)
        for now in range(1040, 1561, 20):
            engine.update(now, {0: reading(0)})
        self.assertEqual(engine.state, "COMPLETE")
        self.assertEqual(engine.duties, [0] * 6)
        engine.stop("User stop")
        engine.update(2000, {0: reading(0)})
        self.assertEqual(engine.state, "STOPPED")

    def test_run_uses_detached_recipe_and_snapshot(self):
        chosen = profile()
        engine = self.make(recipe=chosen)
        chosen["steps"][1]["rpm"] = 1000
        self.initial_zero(engine)
        self.assertEqual(engine.target_rpm, 3000)
        snapshot = engine.snapshot()
        snapshot["fans"][0]["duty"] = 99
        self.assertEqual(engine.duties[0], 0)

    def test_ticks_wrap_preserves_rate_limit(self):
        engine = self.make()
        self.initial_zero(engine)
        engine._last_ms = (1 << 30) - 10
        engine.update(10, {0: reading(1000)})
        self.assertAlmostEqual(engine.duties[0], 0.2)

    def test_invalid_or_stale_measurements_are_not_used_as_real_rpm(self):
        for sample in (reading(float("nan")), reading(float("inf")),
                       reading(-1), reading(3000, age_ms=1600)):
            engine = self.make()
            self.initial_zero(engine)
            engine.update(520, {0: sample})
            self.assertFalse(engine.snapshot()["fans"][0]["valid"])


if __name__ == "__main__":
    unittest.main()
