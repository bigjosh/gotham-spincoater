import importlib.util
from pathlib import Path
import sys
import unittest

DEVICE = Path(__file__).resolve().parents[1] / 'device'
SAVED = sys.path[:]
sys.path.insert(0, str(DEVICE))
try:
    from recipes import DEFAULT_SETTINGS
    SPEC = importlib.util.spec_from_file_location('_control_under_test', DEVICE / 'control.py')
    control = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(control)
finally:
    sys.path[:] = SAVED


def profile(slew=0, dwell=60, rpm=3000, descent=0, final_dwell=0):
    return {'name': 'Test', 'steps': [
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
        {'rpm': rpm, 'slew_s': slew, 'dwell_s': dwell},
        {'rpm': 0, 'slew_s': descent, 'dwell_s': final_dwell},
    ]}


def reading(rpm=None, pulses=None, **extra):
    result = {'rpm': rpm if rpm is not None else 0, 'valid': rpm is not None}
    if pulses is not None:
        result['pulses'] = pulses
    result.update(extra)
    return result


class EngineTests(unittest.TestCase):
    def make(self, enabled=(0,), recipe=None, **settings):
        engine = control.ControlEngine(enabled)
        chosen = dict(DEFAULT_SETTINGS)
        chosen.update(settings)
        engine.start(recipe or profile(), chosen, 0)
        return engine

    def test_idle_and_disabled_channels(self):
        engine = control.ControlEngine((0, 5))
        self.assertFalse(engine.running)
        self.assertEqual(engine.update(100, {}), [0.0] * 6)
        self.assertEqual(engine.participating, (0, 5))
        state = engine.snapshot()
        self.assertEqual([fan['enabled'] for fan in state['fans']],
                         [True, False, False, False, False, True])
        self.assertEqual(state['warning_count'], 0)
        self.assertNotIn('fault_count', state)
        self.assertNotIn('fault', state['fans'][0])

    def test_idle_disabled_and_stopped_channels_keep_reporting_rpm(self):
        engine = control.ControlEngine((0,))
        samples = {i: reading(600 + i * 100, stopped=True) for i in range(6)}
        engine.update(20, samples)
        self.assertEqual(engine.state, 'IDLE')
        self.assertEqual([fan['display_rpm'] for fan in engine.snapshot()['fans']],
                         [600, 700, 800, 900, 1000, 1100])
        engine.start(profile(), DEFAULT_SETTINGS, 40)
        samples[0] = reading(3000)
        engine.update(60, samples)
        self.assertTrue(engine.running)
        self.assertEqual(engine.snapshot()['fans'][5]['display_rpm'], 1100)
        engine.stop('STOP button')
        engine.update(80, {i: reading(500 - i * 50, stopped=True) for i in range(6)})
        self.assertEqual(engine.state, 'STOPPED')
        self.assertEqual(engine.message, 'STOP button')
        self.assertEqual([fan['display_rpm'] for fan in engine.snapshot()['fans']],
                         [500, 450, 400, 350, 300, 250])
        self.assertEqual(engine.duties, [0] * 6)

    def test_display_threshold_keeps_raw_measurement_and_validity(self):
        engine = control.ControlEngine((0,))
        engine.update(20, {0: reading(59.9), 1: reading(60), 2: reading(60.1),
                           3: reading(), 4: reading(3000, age_ms=1600), 5: reading(0)})
        fans = engine.snapshot()['fans']
        self.assertEqual([fan['display_rpm'] for fan in fans], [0, 60, 60.1, 0, 0, 0])
        self.assertEqual(fans[0]['rpm'], 59.9)
        self.assertTrue(fans[0]['valid'])
        self.assertIsNone(fans[3]['rpm'])
        self.assertFalse(fans[3]['valid'])
        self.assertFalse(fans[4]['valid'])

    def test_threshold_configuration_applies_before_start_and_survives_selection_change(self):
        settings = dict(DEFAULT_SETTINGS, rpm_zero_threshold=100)
        engine = control.ControlEngine((0,), settings=settings)
        engine.update(20, {0: reading(80), 1: reading(150)})
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 0)
        settings['rpm_zero_threshold'] = 0  # Constructor copied the settings.
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 0)
        engine.configure_settings(settings)
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 80)
        engine.set_enabled((1,))
        self.assertTrue(engine.snapshot()['fans'][0]['valid'])
        engine.update(40, {0: reading(80), 1: reading(150)})
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 80)
        self.assertFalse(engine.snapshot()['fans'][0]['enabled'])
        invalid = dict(settings, rpm_zero_threshold=-1)
        with self.assertRaises(ValueError):
            engine.configure_settings(invalid)
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 80)
        engine.start(profile(), settings, 60)
        with self.assertRaisesRegex(RuntimeError, 'Stop the recipe'):
            engine.configure_settings(DEFAULT_SETTINGS)

    def test_display_zero_does_not_replace_raw_rpm_in_seeker_or_positive_warning(self):
        engine = self.make(recipe=profile(rpm=100), rpm_zero_threshold=200,
                           tolerance_rpm=1, rpm_warning_delay_s=0)
        engine.update(20, {0: reading(90)})
        fan = engine.snapshot()['fans'][0]
        self.assertEqual(fan['display_rpm'], 0)
        self.assertEqual(fan['rpm'], 90)
        self.assertTrue(fan['valid'])
        self.assertAlmostEqual(fan['duty'], .002)  # 10 RPM error, not 100 RPM.
        self.assertTrue(fan['warning'])
        self.assertEqual(fan['error_percent'], 10)
        before = fan['duty']
        engine.update(40, {0: reading()})
        self.assertEqual(engine.duties[0], before)
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])

    def test_empty_selection_is_idle_but_cannot_start(self):
        engine = control.ControlEngine(())
        self.assertEqual(engine.update(20, {}), [0] * 6)
        with self.assertRaisesRegex(ValueError, 'Enable at least one fan'):
            engine.start(profile(), DEFAULT_SETTINGS, 0)
        self.assertFalse(engine.running)

    def test_selection_only_changes_after_stop(self):
        engine = self.make(enabled=(0, 2))
        with self.assertRaisesRegex(RuntimeError, 'Stop the recipe'):
            engine.set_enabled((0,))
        engine.update(20, {0: reading(1000), 2: reading(1000)})
        engine.stop()
        engine.set_enabled((0,))
        self.assertEqual(engine.enabled, (0,))
        self.assertEqual(engine.duties, [0] * 6)
        self.assertTrue(engine.snapshot()['fans'][2]['valid'])
        self.assertEqual(engine.snapshot()['fans'][2]['display_rpm'], 1000)
        self.assertEqual(engine.snapshot()['warning_count'], 0)

    def test_invalid_selection_preserves_previous_selection(self):
        engine = control.ControlEngine((0, 1))
        for invalid in ((0, 0), (True,), (6,), (-1,)):
            with self.assertRaises(ValueError):
                engine.set_enabled(invalid)
            self.assertEqual(engine.enabled, (0, 1))

    def test_disabled_readings_do_not_stop_or_warn(self):
        engine = self.make(enabled=(0,), rpm_warning_delay_s=0)
        engine.update(20, {0: reading(3000), 1: reading(stopped=True, overflow=True)})
        self.assertTrue(engine.running)
        self.assertEqual(engine.snapshot()['warning_count'], 0)
        self.assertEqual(engine.duties[1], 0)

    def test_independent_seekers_and_configurable_rate_limit(self):
        engine = self.make(enabled=(0, 1), max_power_per_s=8)
        for now in range(20, 1001, 20):
            duties = engine.update(now, {0: reading(1000), 1: reading(2900)})
        self.assertAlmostEqual(duties[0], 8)
        self.assertAlmostEqual(duties[1], 1)
        self.assertEqual(duties[2:], [0] * 4)

    def test_loop_stall_does_not_create_large_power_jump(self):
        engine = self.make()
        engine.update(20, {0: reading(1000)})
        before = engine.duties[0]
        engine.update(5020, {0: reading(1000)})
        self.assertAlmostEqual(engine.duties[0] - before, 1.0)
        self.assertAlmostEqual(engine.snapshot()['phase_remaining_s'], 54.98)

    def test_deadband_is_separate_from_percentage_warning(self):
        engine = self.make(rpm_warning_percent=5, rpm_warning_delay_s=0)
        engine.update(20, {0: reading(2900)})
        fan = engine.snapshot()['fans'][0]
        self.assertGreater(fan['duty'], 0)  # Outside the 50 RPM seeker deadband.
        self.assertTrue(fan['in_bounds'])  # Inside the 150 RPM warning band.
        self.assertFalse(fan['warning'])
        before = engine.duties[0]
        engine.update(40, {0: reading(2975)})
        self.assertEqual(engine.duties[0], before)

    def test_fixed_ramp_dwell_descent_schedule_ignores_measured_rpm(self):
        engine = self.make(enabled=(0, 1), recipe=profile(slew=2, dwell=3, descent=2))
        self.assertEqual(engine.state, 'RAMP')
        self.assertEqual(engine.snapshot()['step'], 2)
        engine.update(1000, {0: reading(0), 1: reading()})
        self.assertEqual(engine.target_rpm, 1500)
        self.assertEqual(engine.snapshot()['phase_remaining_s'], 1)
        engine.update(2000, {0: reading(1000), 1: reading()})
        self.assertEqual(engine.state, 'DWELL')
        self.assertEqual(engine.target_rpm, 3000)
        self.assertEqual(engine.snapshot()['phase_remaining_s'], 3)
        engine.update(5000, {0: reading(1000), 1: reading()})
        self.assertEqual(engine.state, 'RAMP')
        self.assertEqual(engine.snapshot()['step'], 3)
        engine.update(6000, {0: reading(1000), 1: reading()})
        self.assertEqual(engine.target_rpm, 1500)
        engine.update(7000, {0: reading(1000), 1: reading()})
        self.assertEqual(engine.state, 'COMPLETE')
        self.assertEqual(engine.duties, [0] * 6)
        self.assertEqual(engine.snapshot()['warning_count'], 0)
        self.assertEqual(engine.participating, (0, 1))

    def test_six_out_of_range_fans_stay_selected_and_keep_seeking(self):
        engine = self.make(enabled=tuple(range(6)), rpm_warning_delay_s=.2)
        for now in range(20, 20001, 20):
            engine.update(now, {i: reading(1000 + 100 * i) for i in range(6)})
        self.assertEqual(engine.state, 'DWELL')
        self.assertEqual(engine.participating, tuple(range(6)))
        self.assertEqual(engine.snapshot()['warning_count'], 6)
        self.assertTrue(all(duty == 100 for duty in engine.duties))
        self.assertAlmostEqual(engine.snapshot()['phase_remaining_s'], 40)

    def test_warning_delay_and_first_good_sample_recovery(self):
        engine = self.make(rpm_warning_delay_s=2)
        engine.update(0, {0: reading(3000)})
        engine.update(100, {0: reading(2700)})
        engine.update(2080, {0: reading(2700)})
        self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        engine.update(2100, {0: reading(2700)})
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])
        self.assertEqual(engine.snapshot()['fans'][0]['out_of_bounds_s'], 2)
        engine.update(2120, {0: reading(3000)})
        fan = engine.snapshot()['fans'][0]
        self.assertFalse(fan['warning'])
        self.assertTrue(fan['in_bounds'])
        self.assertEqual(fan['out_of_bounds_s'], 0)
        engine.update(2140, {0: reading(2700)})
        engine.update(4120, {0: reading(2700)})
        self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        engine.update(4140, {0: reading(2700)})
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])

    def test_brief_in_bounds_samples_reset_warning_clock(self):
        engine = self.make(rpm_warning_delay_s=1)
        for now in range(0, 20001, 20):
            engine.update(now, {0: reading(3000 if now % 500 == 0 else 2000)})
            self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        self.assertTrue(engine.running)
        self.assertGreater(engine.duties[0], 0)

    def test_warning_percentage_boundary_and_zero_delay(self):
        engine = self.make(rpm_warning_percent=10, rpm_warning_delay_s=0)
        for rpm in (2700, 3300):
            engine.update(20, {0: reading(rpm)})
            self.assertEqual(engine.snapshot()['fans'][0]['error_percent'], 10)
            self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        for rpm in (2699, 3301):
            engine.update(40, {0: reading(rpm)})
            self.assertTrue(engine.snapshot()['fans'][0]['warning'])

    def test_six_warning_timers_are_independent(self):
        engine = self.make(enabled=tuple(range(6)), rpm_warning_delay_s=.5)
        engine.update(0, {i: reading(3000) for i in range(6)})
        for now in range(20, 701, 20):
            engine.update(now, {i: reading(2000 if now >= 20 + 100 * i else 3000)
                                for i in range(6)})
        self.assertEqual([f['warning'] for f in engine.snapshot()['fans']],
                         [True, True, False, False, False, False])
        engine.update(720, {i: reading(3000 if i == 0 else 2000) for i in range(6)})
        self.assertEqual([f['warning'] for f in engine.snapshot()['fans']],
                         [False, True, True, False, False, False])

    def test_warning_compares_to_instantaneous_ramp_target(self):
        engine = self.make(recipe=profile(slew=2), rpm_warning_delay_s=0)
        engine.update(1000, {0: reading(1500)})
        fan = engine.snapshot()['fans'][0]
        self.assertEqual(fan['target_rpm'], 1500)
        self.assertEqual(fan['error_percent'], 0)
        self.assertFalse(fan['warning'])
        engine.update(1500, {0: reading(1500)})
        self.assertEqual(engine.snapshot()['fans'][0]['target_rpm'], 2250)
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])

    def test_late_tick_skips_overdue_phases_and_only_seeks_current_target(self):
        engine = self.make(recipe=profile(slew=1, dwell=1, descent=1, final_dwell=1))
        engine.update(500, {0: reading(0)})
        before = engine.duties[0]
        engine.update(2500, {0: reading(1500)})
        self.assertEqual(engine.snapshot()['step'], 3)
        self.assertEqual(engine.state, 'RAMP')
        self.assertEqual(engine.target_rpm, 1500)
        self.assertEqual(engine.duties[0], before)
        engine.update(9000, {0: reading(1000)})
        self.assertEqual(engine.state, 'COMPLETE')
        self.assertEqual(engine.duties, [0] * 6)

    def test_late_tick_into_zero_dwell_updates_warning_target_and_zeroes_power(self):
        engine = self.make(recipe=profile(dwell=1, final_dwell=5), rpm_warning_delay_s=0)
        engine.update(20, {0: reading(1000)})
        self.assertGreater(engine.duties[0], 0)
        engine.update(1500, {0: reading(1000)})
        fan = engine.snapshot()['fans'][0]
        self.assertEqual(engine.snapshot()['step'], 3)
        self.assertEqual(fan['target_rpm'], 0)
        self.assertEqual(fan['duty'], 0)
        self.assertIsNone(fan['error_percent'])
        self.assertTrue(fan['warning'])

    def test_zero_duration_steps_are_bounded_and_never_command_positive_power(self):
        chosen = {'name': 'Instant', 'steps': [
            {'rpm': 0 if i in (0, 8) else 3000, 'slew_s': 0, 'dwell_s': 0}
            for i in range(9)]}
        engine = self.make(recipe=chosen)
        self.assertEqual(engine.state, 'COMPLETE')
        self.assertEqual(engine.snapshot()['step'], 9)
        self.assertEqual(engine.update(20, {0: reading(0)}), [0] * 6)

    def test_long_schedule_preserves_exact_deadline_with_fractional_second_ticks(self):
        engine = self.make(recipe=profile(dwell=1800))
        for now in range(17, 1800000, 17):
            engine.update(now, {0: reading(1000)})
        engine.update(1799999, {0: reading(1000)})
        self.assertTrue(engine.running)
        self.assertAlmostEqual(engine.snapshot()['phase_remaining_s'], .001)
        engine.update(1800000, {0: reading(1000)})
        self.assertEqual(engine.state, 'COMPLETE')
        self.assertEqual(engine.snapshot()['elapsed_s'], 1800)
        self.assertEqual(engine.duties, [0] * 6)

    def test_startup_without_tach_is_bounded_but_never_excluded_or_stopped(self):
        engine = self.make(max_power_per_s=100)
        for now in range(20, 20001, 20):
            engine.update(now, {0: reading()})
        self.assertEqual(engine.duties[0], 30)
        self.assertTrue(engine.running)
        self.assertEqual(engine.participating, (0,))
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])
        self.assertIsNone(engine.snapshot()['fans'][0]['error_percent'])
        engine.update(20020, {0: reading(600)})
        self.assertGreater(engine.duties[0], 30)

    def test_lost_tach_holds_power_then_resumes_seeking_when_reading_returns(self):
        engine = self.make()
        for now in range(20, 1001, 20):
            engine.update(now, {0: reading(1000)})
        before = engine.duties[0]
        for now in range(1020, 20001, 20):
            engine.update(now, {0: reading()})
            self.assertEqual(engine.duties[0], before)
        self.assertTrue(engine.running)
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])
        engine.update(20020, {0: reading(1000)})
        self.assertGreater(engine.duties[0], before)

    def test_low_power_zero_descent_without_tach_only_removes_power(self):
        engine = self.make(recipe=profile(dwell=1, descent=120))
        engine.update(0, {0: reading(3000)})
        engine.update(1000, {0: reading(3000)})
        engine.duties[0] = 20
        previous = 20
        for now in range(1020, 5021, 20):
            engine.update(now, {0: reading()})
            self.assertLessEqual(engine.duties[0], previous)
            self.assertLessEqual(previous - engine.duties[0], .200001)
            previous = engine.duties[0]
        self.assertEqual(engine.state, 'RAMP')
        self.assertEqual(engine.duties[0], 0)
        self.assertTrue(engine.snapshot()['coasting_without_tach'])

    def test_driver_error_is_separate_from_rpm_warning_and_selection(self):
        engine = self.make(enabled=(0, 1), rpm_warning_delay_s=0)
        engine.update(20, {0: reading(1000), 1: reading(1000)})
        for now in range(40, 1041, 20):
            engine.update(now, {0: reading(1000), 1: reading(3000, driver_error='command_underrun')})
        self.assertTrue(engine.running)
        self.assertEqual(engine.participating, (0, 1))
        self.assertGreater(engine.duties[0], 0)
        self.assertEqual(engine.duties[1], 0)
        self.assertFalse(engine.snapshot()['fans'][1]['valid'])
        self.assertTrue(engine.snapshot()['fans'][1]['warning'])

    def test_driver_stop_flag_is_not_shared_emergency_stop(self):
        engine = self.make(enabled=(0, 1))
        engine.update(20, {0: reading(1000), 1: reading(stopped=True, fault='command_underrun')})
        self.assertTrue(engine.running)
        self.assertGreater(engine.duties[0], 0)
        self.assertEqual(engine.duties[1], 0)
        engine.update(40, {0: reading(stopped=True), 1: reading(driver_error='command_underrun')})
        self.assertEqual(engine.state, 'STOPPED')
        self.assertEqual(engine.message, 'Hardware emergency stop')
        self.assertEqual(engine.duties, [0] * 6)

    def test_stop_is_latched_until_start_and_clears_warnings(self):
        engine = self.make(enabled=(0, 1), rpm_warning_delay_s=0)
        engine.update(20, {0: reading(1000), 1: reading(1000)})
        self.assertEqual(engine.snapshot()['warning_count'], 2)
        engine.stop('STOP button')
        engine.update(40, {0: reading(stopped=True), 1: reading(stopped=True)})
        self.assertEqual(engine.state, 'STOPPED')
        self.assertEqual(engine.message, 'STOP button')
        self.assertEqual(engine.duties, [0] * 6)
        self.assertEqual(engine.snapshot()['warning_count'], 0)
        engine.start(profile(), DEFAULT_SETTINGS, 60)
        self.assertTrue(engine.running)
        self.assertEqual(engine.participating, (0, 1))
        self.assertEqual(engine.snapshot()['warning_count'], 0)

    def test_zero_target_uses_seeker_tolerance_and_ignores_display_threshold(self):
        engine = self.make(recipe=profile(rpm=0), rpm_warning_delay_s=0, tolerance_rpm=100)
        engine.update(20, {0: reading(60)})
        self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        self.assertIsNone(engine.snapshot()['fans'][0]['error_percent'])
        engine.update(40, {0: reading(100)})
        self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        self.assertTrue(engine.snapshot()['fans'][0]['in_bounds'])
        engine.update(60, {0: reading(101)})
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])

    def test_zero_display_threshold_does_not_change_zero_target_warning(self):
        engine = self.make(recipe=profile(rpm=0), rpm_warning_delay_s=0, rpm_zero_threshold=0)
        engine.update(20, {0: reading(.1)})
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], .1)
        self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        engine.update(30, {0: reading(51)})
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])
        engine.update(40, {0: reading(0)})
        self.assertFalse(engine.snapshot()['fans'][0]['warning'])

    def test_high_display_threshold_cannot_turn_spinning_fan_into_in_bounds_zero(self):
        engine = self.make(recipe=profile(rpm=0), rpm_warning_delay_s=0, rpm_zero_threshold=500)
        engine.update(20, {0: reading(100)})
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 0)
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])

    def test_start_and_selection_preserve_period_diagnostics_without_refreshing_age(self):
        engine = control.ControlEngine((0,))
        engine.update(1000, {0: reading(3000, pulses=120, period_us=10000, samples=8, age_ms=200)})
        engine.set_enabled((1,))
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 3000)
        engine.start(profile(), DEFAULT_SETTINGS, 1050)
        fan = engine.snapshot()['fans'][0]
        self.assertEqual((fan['period_us'], fan['samples'], fan['pulses'], fan['raw_rpm']),
                         (10000, 8, 120, 3000))
        self.assertEqual(fan['age_ms'], 250)
        self.assertTrue(fan['valid'])
        engine.update(1100, {})
        self.assertEqual(engine.snapshot()['fans'][0]['age_ms'], 300)
        engine.update(2300, {})
        fan = engine.snapshot()['fans'][0]
        self.assertEqual(fan['age_ms'], 1500)
        self.assertFalse(fan['valid'])
        self.assertEqual(fan['display_rpm'], 0)
        self.assertEqual(fan['raw_rpm'], 3000)

    def test_negative_producer_rpm_is_retained_for_diagnosis_but_never_displayed(self):
        engine = control.ControlEngine((0,))
        engine.update(20, {0: reading(-123, pulses=12, period_us=-243902.4, samples=8, age_ms=0)})
        fan = engine.snapshot()['fans'][0]
        self.assertEqual(fan['raw_rpm'], -123)
        self.assertEqual(fan['period_us'], -243902.4)
        self.assertEqual(fan['samples'], 8)
        self.assertEqual(fan['pulses'], 12)
        self.assertFalse(fan['valid'])
        self.assertIsNone(fan['rpm'])
        self.assertEqual(fan['display_rpm'], 0)

    def test_retained_capture_age_survives_ticks_wrap(self):
        engine = control.ControlEngine((0,))
        engine.update((1 << 30) - 100, {0: reading(3000, age_ms=50)})
        engine.start(profile(), DEFAULT_SETTINGS, 40)
        self.assertEqual(engine.snapshot()['fans'][0]['age_ms'], 190)
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 3000)

    def test_unknown_display_zero_does_not_hide_recent_tach_activity_or_driver_error(self):
        engine = self.make(recipe=profile(rpm=0), rpm_warning_delay_s=0)
        engine.update(0, {0: reading(pulses=0)})
        engine.update(1000, {0: reading(pulses=1)})
        engine.update(2000, {0: reading(pulses=1)})
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 0)
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])
        engine.update(2500, {0: reading(pulses=1)})
        self.assertFalse(engine.snapshot()['fans'][0]['warning'])
        engine.update(10000, {0: reading(driver_error='sampling unavailable')})
        self.assertEqual(engine.snapshot()['fans'][0]['display_rpm'], 0)
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])

    def test_zero_quiet_clears_warning_without_claiming_measured_rpm(self):
        engine = self.make(recipe=profile(rpm=0), rpm_warning_delay_s=.2)
        for now in range(0, 1501, 20):
            engine.update(now, {0: reading(pulses=0)})
        fan = engine.snapshot()['fans'][0]
        self.assertTrue(engine.snapshot()['standstill_assumed'])
        self.assertFalse(fan['valid'])
        self.assertIsNone(fan['rpm'])
        self.assertIsNone(fan['error_percent'])
        self.assertTrue(fan['in_bounds'])
        self.assertFalse(fan['warning'])

    def test_coasting_pulses_and_overflow_prevent_false_zero_warning_clear(self):
        for overflow in (False, True):
            engine = self.make(recipe=profile(rpm=0), rpm_warning_delay_s=.2)
            for now in range(0, 3021, 20):
                pulses = 0 if overflow else now // 100
                engine.update(now, {0: reading(pulses=pulses, overflow=overflow)})
            self.assertFalse(engine.snapshot()['standstill_assumed'])
            self.assertFalse(engine.snapshot()['fans'][0]['in_bounds'])
            self.assertTrue(engine.snapshot()['fans'][0]['warning'])
            for now in range(3040, 4541, 20):
                engine.update(now, {0: reading(pulses=pulses)})
            self.assertTrue(engine.snapshot()['fans'][0]['in_bounds'])
            self.assertFalse(engine.snapshot()['fans'][0]['warning'])

    def test_warning_and_power_timing_survive_ticks_wrap(self):
        engine = self.make(rpm_warning_delay_s=.1)
        engine._last_ms = (1 << 30) - 60
        engine._outside_since[0] = (1 << 30) - 60
        engine.update((1 << 30) - 40, {0: reading(1000)})
        self.assertAlmostEqual(engine.duties[0], .2)
        engine.update(40, {0: reading(1000)})
        self.assertTrue(engine.snapshot()['fans'][0]['warning'])
        self.assertAlmostEqual(engine.snapshot()['fans'][0]['out_of_bounds_s'], .1)

    def test_invalid_or_stale_measurements_are_missing_tach_and_warn(self):
        for sample in (reading(float('nan')), reading(float('inf')), reading(-1),
                       reading(True), reading(3000, age_ms=1600), reading(3000, overflow=True)):
            engine = self.make(rpm_warning_delay_s=0)
            engine.update(20, {0: sample})
            self.assertFalse(engine.snapshot()['fans'][0]['valid'])
            self.assertTrue(engine.snapshot()['fans'][0]['warning'])
            self.assertGreater(engine.duties[0], 0)

    def test_run_uses_detached_recipe_and_snapshot(self):
        chosen = profile()
        engine = self.make(recipe=chosen)
        chosen['steps'][1]['rpm'] = 1000
        self.assertEqual(engine.target_rpm, 3000)
        snapshot = engine.snapshot()
        snapshot['fans'][0]['duty'] = 99
        self.assertEqual(engine.duties[0], 0)


if __name__ == '__main__':
    unittest.main()
