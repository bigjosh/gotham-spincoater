"""Control-core lifecycle and deterministic START/edit race checks."""
import importlib.util
from pathlib import Path
import sys
import threading
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self):
        self.now = 0
        self.on_sleep = None

    def ticks_ms(self):
        return self.now

    def ticks_us(self):
        return self.now * 1000

    def sleep_ms(self, delay):
        self.now += delay
        if self.on_sleep:
            self.on_sleep()


class FakePin:
    IN, OUT, PULL_UP = 0, 1, 2
    levels = {}
    modes = {}

    def __init__(self, number, mode=None, pull=None):
        self.number = number
        self.levels.setdefault(number, 1)
        if mode is not None:
            self.init(mode, pull)

    def init(self, mode, pull=None, value=None):
        previous = self.modes.get(self.number)
        self.modes[self.number] = mode
        if value is not None:
            self.levels[self.number] = value
        elif mode == self.IN and pull == self.PULL_UP and previous == self.OUT:
            self.levels[self.number] = 1

    def value(self):
        return self.levels[self.number]


class FakeFan:
    all = []
    fail_number = None

    def __init__(self, pwm, tach, **options):
        if pwm == self.fail_number:
            raise RuntimeError("constructor failed")
        self.pwm, self.tach = pwm, tach
        self.arms = 0
        self.stops = 0
        self.closed = False
        self.stopped = False
        self.output = 0
        self.on_arm = None
        self.on_sample = None
        self.on_set_duty = None
        self.on_stop = None
        self.arm_result = True
        self.fault = None
        self.rpm = 0
        self.valid = False
        self.samples = 0
        self.commands = []
        self.on_close = None
        self.all.append(self)

    def arm(self):
        self.arms += 1
        if self.on_arm:
            self.on_arm()
        self.stopped = not self.arm_result
        return self.arm_result

    def sample(self):
        self.samples += 1
        if self.on_sample:
            self.on_sample()
        return {"rpm": self.rpm, "valid": self.valid,
                "pulses": self.samples if self.valid else 0,
                "stopped": self.stopped, "fault": self.fault}

    def set_duty(self, value):
        if self.on_set_duty:
            self.on_set_duty()
        if self.stopped:
            self.output = 0
            return 0
        self.commands.append(value)
        self.output = value
        return value

    def stop(self):
        if self.on_stop:
            self.on_stop()
        self.stops += 1
        self.stopped = True
        self.output = 0

    def close(self):
        if self.on_close:
            self.on_close()
        self.stop()
        self.closed = True


class ButtonScript:
    def __init__(self, press=True):
        self.press = press

    def pressed(self, now):
        result = self.press
        self.press = False
        return result

    def block_until_release(self):
        self.press = False


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        FakePin.levels = {}
        FakePin.modes = {}
        FakeFan.all = []
        FakeFan.fail_number = None
        time_module = types.ModuleType("time")
        for name in ("ticks_ms", "ticks_us", "sleep_ms"):
            setattr(time_module, name, getattr(self.clock, name))
        time_module.ticks_diff = lambda new, old: new - old
        time_module.ticks_add = lambda now, delta: now + delta
        thread_module = types.ModuleType("_thread")
        thread_module.allocate_lock = threading.Lock
        thread_module.stack_size = lambda size: None
        thread_module.start_new_thread = lambda function, arguments: None
        self.thread_module = thread_module
        config = types.ModuleType("config")
        config.ENABLED_CHANNELS = (0,)
        config.AUX_LINKS_DISCONNECTED = False
        config.FUTURE_CHANNELS = ((18, 19), (20, 21), (22, 28), (1, 0), (13, 12), (16, 17))
        config.TACH_STATE_MACHINES = (0, 1, 2, 3, 8, 9)
        config.BUTTON_START, config.BUTTON_STOP, config.PERIOD_AVERAGE = 15, 14, 8
        config.PULSES_PER_REV = 2
        self.config = config
        machine = types.ModuleType("machine")
        machine.Pin = FakePin
        pio_fan = types.ModuleType("pio_fan")
        pio_fan.PioFan = FakeFan
        self.modules = patch.dict(sys.modules, {
            "time": time_module, "_thread": thread_module, "config": config,
            "machine": machine, "pio_fan": pio_fan,
        })
        self.modules.start()
        self.paths = patch.object(sys, "path", [str(ROOT / "device")] + sys.path)
        self.paths.start()
        from recipes import RecipeBook, validate_document
        self.validate_document = validate_document
        self.book = RecipeBook()
        self.save_hook = None
        self.saved = 0

        def save(data):
            if self.save_hook:
                self.save_hook()
            self.book.data = validate_document(data)
            self.saved += 1

        self.book.save = save
        spec = importlib.util.spec_from_file_location("_runtime_test", ROOT / "device" / "runtime.py")
        self.runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runtime)
        self.controllers = []
        self.folder = tempfile.TemporaryDirectory()

    def tearDown(self):
        for controller in self.controllers:
            controller.close()
        self.paths.stop()
        self.modules.stop()
        self.folder.cleanup()

    def controller(self):
        from fan_settings import FanSettings
        settings = FanSettings(defaults=self.config.ENABLED_CHANNELS,
                               available=tuple(range(6 if self.config.AUX_LINKS_DISCONNECTED else 4)),
                               path=str(Path(self.folder.name) / 'fans.json'))
        controller = self.runtime.Controller(self.book, fan_settings=settings)
        self.controllers.append(controller)
        return controller

    def one_tick(self, controller, start=True):
        controller.start_button = ButtonScript(start)
        self.clock.on_sleep = lambda: setattr(controller, "closing", True)
        try:
            controller._run()
        finally:
            self.clock.on_sleep = None

    def run_for(self, controller, duration_ms, on_sleep=None, start=True):
        controller.start_button = ButtonScript(start)
        end = self.clock.now + duration_ms

        def tick():
            if on_sleep:
                on_sleep()
            if self.clock.now >= end:
                controller.closing = True

        self.clock.on_sleep = tick
        try:
            controller._run()
        finally:
            self.clock.on_sleep = None

    def changed_data(self):
        data = self.validate_document(self.book.data)
        data["settings"]["max_power_per_s"] = 7
        return data

    def warning_recipe(self):
        data = self.validate_document(self.book.data)
        data['settings']['rpm_warning_percent'] = 10
        data['settings']['rpm_warning_delay_s'] = 0.2
        data['profiles'][0]['steps'] = [
            {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
            {'rpm': 2000, 'slew_s': 0, 'dwell_s': 30},
            {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
        ]
        self.book.data = self.validate_document(data)

    def test_missing_tach_warns_but_continues_driving_and_recovery_clears_warning(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        self.warning_recipe()
        controller = self.controller()
        controller.fans[1].valid = True
        controller.fans[1].rpm = 2000
        observed = []

        def monitor():
            if self.clock.now in (20000, 20300):
                observed.append((controller.snapshot(), controller.fans[0].output,
                                 controller.fans[0].stopped, len(controller.fans[0].commands)))
            if self.clock.now == 20020:
                controller.fans[0].valid = True
                controller.fans[0].rpm = 2000

        self.run_for(controller, 20400, monitor)
        before, after = observed
        self.assertTrue(before[0]['running'])
        self.assertTrue(before[0]['fans'][0]['warning'])
        self.assertTrue(before[0]['fans'][0]['participating'])
        self.assertIsNone(before[0]['fans'][0]['driver_error'])
        self.assertFalse(before[0]['fans'][1]['warning'])
        self.assertGreater(before[1], 0)
        self.assertFalse(before[2])
        self.assertTrue(after[0]['running'])
        self.assertFalse(after[0]['fans'][0]['warning'])
        self.assertTrue(after[0]['fans'][0]['in_bounds'])
        self.assertGreater(after[1], 0)
        self.assertFalse(after[2])
        self.assertGreater(after[3], before[3])
        self.assertEqual(controller.enabled, (0, 1))
        self.assertEqual(controller.fan_settings.load(), (0, 1))
        self.assertNotIn('fault', after[0]['fans'][0])
        self.assertNotIn('fault_count', after[0])

    def test_measured_off_target_warning_never_stops_either_output(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        self.warning_recipe()
        controller = self.controller()
        for fan in controller.fans.values():
            fan.valid = True
            fan.rpm = 500
        observed = []

        def monitor():
            if self.clock.now in (1000, 19000):
                observed.append((controller.snapshot(),
                                 tuple(controller.fans[i].output for i in (0, 1)),
                                 tuple(controller.fans[i].stopped for i in (0, 1))))

        self.run_for(controller, 19100, monitor)
        for state, outputs, stopped in observed:
            self.assertTrue(state['running'])
            self.assertEqual(state['warning_count'], 2)
            self.assertTrue(all(state['fans'][i]['participating'] for i in (0, 1)))
            self.assertTrue(all(state['fans'][i]['driver_error'] is None for i in (0, 1)))
            self.assertTrue(all(output > 0 for output in outputs))
            self.assertEqual(stopped, (False, False))
        self.assertGreaterEqual(observed[1][1][0], observed[0][1][0])

    def test_constructor_allocates_available_fans_at_zero_and_parks_disabled(self):
        controller = self.controller()
        self.assertEqual(list(controller.fans), [0, 1, 2, 3])
        self.assertTrue(all(fan.output == 0 for fan in controller.fans.values()))
        self.assertTrue(all(controller.fans[i].stopped for i in (1, 2, 3)))
        self.assertFalse(controller.fans[0].stopped)
        self.assertEqual([f['available'] for f in controller.snapshot()['fans']],
                         [True, True, True, True, False, False])
        self.assertFalse(controller.snapshot()["running"])

    def test_partial_constructor_failure_closes_prior_fans(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        FakeFan.fail_number = 20
        with self.assertRaises(RuntimeError):
            self.controller()
        self.assertEqual(len(FakeFan.all), 1)
        self.assertTrue(FakeFan.all[0].closed)

    def test_idle_edit_publishes_detached_next_run_configuration(self):
        controller = self.controller()
        data = self.changed_data()
        controller.save_config(data)
        self.assertEqual(controller._settings["max_power_per_s"], 7)
        self.assertFalse(controller.editing)
        data["settings"]["max_power_per_s"] = 90
        self.assertEqual(controller._settings["max_power_per_s"], 7)

    def test_running_edit_is_rejected_before_writing(self):
        controller = self.controller()
        controller._running = True
        with self.assertRaises(RuntimeError):
            controller.save_config(self.changed_data())
        self.assertEqual(self.saved, 0)

    def test_invalid_edit_releases_lock_and_preserves_old_config(self):
        controller = self.controller()
        data = self.changed_data()
        data["settings"]["max_power_per_s"] = float("nan")
        with self.assertRaises(ValueError):
            controller.save_config(data)
        self.assertFalse(controller.editing)
        self.assertEqual(controller._settings["max_power_per_s"], 10)
        controller.save_config(self.changed_data())
        self.assertEqual(self.saved, 1)

    def test_start_claim_prevents_edit_before_engine_start(self):
        controller = self.controller()
        observed = []

        def attempt_save():
            observed.append(controller._running)
            with self.assertRaises(RuntimeError):
                controller.save_config(self.changed_data())

        controller.fans[0].on_arm = attempt_save
        self.one_tick(controller)
        self.assertEqual(observed, [True])
        self.assertEqual(controller.fans[0].arms, 1)
        self.assertEqual(self.saved, 0)

    def test_start_during_edit_is_consumed_without_arming(self):
        controller = self.controller()

        def start_during_flash_save():
            self.assertTrue(controller.editing)
            self.one_tick(controller)

        self.save_hook = start_during_flash_save
        controller.save_config(self.changed_data())
        self.assertEqual(controller.fans[0].arms, 0)
        self.assertFalse(controller.engine.running)
        self.assertFalse(controller.editing)

    def test_stop_has_priority_over_simultaneous_start(self):
        controller = self.controller()
        FakePin.levels[14] = 0
        self.one_tick(controller)
        self.assertEqual(controller.fans[0].arms, 0)
        self.assertEqual(controller.engine.state, "STOPPED")
        self.assertEqual(controller.engine.message, "STOP button")

    def test_driver_error_parks_only_failed_hardware_and_keeps_healthy_power_running(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        observed = []

        def monitor():
            if self.clock.now == 2300:
                controller.fans[1].fault = 'command_underrun'
                controller.fans[1].stopped = True
            if self.clock.now in (2400, 2600):
                observed.append((controller.snapshot(),
                                 controller.fans[0].stopped,
                                 controller.fans[0].output,
                                 controller.fans[1].stopped,
                                 controller.fans[1].output,
                                 controller.fans[1].samples))

        self.run_for(controller, 2700, monitor)
        self.assertEqual(len(observed), 2)
        for state, healthy_stopped, healthy_output, failed_stopped, failed_output, _ in observed:
            self.assertTrue(state['running'])
            self.assertIsNone(state['fans'][0]['driver_error'])
            self.assertEqual(state['fans'][1]['driver_error'], 'command_underrun')
            self.assertTrue(state['fans'][1]['participating'])
            self.assertTrue(state['fans'][1]['enabled'])
            self.assertFalse(healthy_stopped)
            self.assertGreater(healthy_output, 0)
            self.assertTrue(failed_stopped)
            self.assertEqual(failed_output, 0)
        self.assertEqual(observed[0][-1], observed[1][-1])
        self.assertEqual(controller.enabled, (0, 1))
        self.assertEqual(controller.fan_settings.load(), (0, 1))

    def test_all_selected_arm_failures_end_run_at_zero(self):
        controller = self.controller()
        controller.fans[0].arm_result = False
        self.one_tick(controller)
        self.assertFalse(controller.engine.running)
        self.assertEqual(controller.fans[0].output, 0)

    def test_refused_arm_stops_all_even_when_stop_pin_is_high_again(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        controller.fans[0].arm_result = False
        self.one_tick(controller)
        state = controller.snapshot()
        self.assertFalse(state['running'])
        self.assertIn('STOP or clock change', state['message'])
        self.assertEqual(controller.fans[0].samples, 0)
        self.assertEqual(controller.fans[0].commands, [])
        self.assertEqual(controller.fans[1].arms, 0)
        self.assertEqual(controller.fans[1].commands, [])

    def test_arm_exception_isolated_while_other_selected_fan_starts(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        def fail():
            raise OSError('DMA unavailable')
        controller.fans[0].on_arm = fail
        self.one_tick(controller)
        self.assertTrue(controller.snapshot()['running'])
        self.assertIn('DMA unavailable', controller.snapshot()['fans'][0]['driver_error'])
        self.assertEqual(controller.fans[0].commands, [])
        self.assertEqual(len(controller.fans[1].commands), 1)

    def test_sample_exception_isolated_and_retained_in_published_driver_error(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        def fail():
            raise OSError('RX failure')
        controller.fans[0].on_sample = fail
        self.run_for(controller, 220)
        self.assertTrue(controller.snapshot()['running'])
        self.assertIn('RX failure', controller.snapshot()['fans'][0]['driver_error'])
        self.assertEqual(controller.fans[0].samples, 1)
        self.assertEqual(controller.fans[0].commands, [])
        self.assertGreater(len(controller.fans[1].commands), 1)

    def test_power_exceptions_hold_broken_outputs_zero_without_stopping_recipe(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        def fail():
            raise OSError('TX failure')
        controller.fans[0].on_set_duty = fail
        observed = []
        def monitor():
            if self.clock.now == 110:
                observed.append(controller.snapshot())
            if self.clock.now == 120:
                controller.fans[1].on_set_duty = fail
        self.run_for(controller, 220, monitor)
        self.assertTrue(observed[0]['running'])
        self.assertIn('TX failure', observed[0]['fans'][0]['driver_error'])
        self.assertEqual(controller.fans[0].samples, 1)
        self.assertEqual(controller.fans[0].commands, [])
        self.assertGreater(len(controller.fans[1].commands), 1)
        self.assertNotEqual(controller.snapshot()['state'], 'ERROR')
        self.assertTrue(controller.snapshot()['running'])
        self.assertTrue(all(controller.snapshot()['fans'][i]['duty'] == 0 for i in (0, 1)))
        self.assertEqual(controller.engine.duties, [0] * 6)

    def test_fresh_start_retries_selected_hardware_and_clears_driver_error(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        def fail():
            raise OSError('DMA unavailable')
        controller.fans[0].on_arm = fail
        self.one_tick(controller)
        self.assertIsNotNone(controller.snapshot()['fans'][0]['driver_error'])
        controller.engine.stop('STOP button')
        controller.closing = controller.finished = False
        controller.fans[0].on_arm = None
        self.one_tick(controller)
        self.assertTrue(controller.snapshot()['running'])
        self.assertIsNone(controller.snapshot()['fans'][0]['driver_error'])
        self.assertTrue(controller.snapshot()['fans'][0]['participating'])
        self.assertEqual(controller.fans[0].arms, 2)
        self.assertEqual(controller.fans[1].arms, 2)
        self.assertEqual(len(controller.fans[0].commands), 1)

    def test_failed_isolation_stop_asserts_global_hardware_stop(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        def fail():
            raise OSError('driver stop failed')
        controller.fans[0].fault = 'command_underrun'
        controller.fans[0].on_stop = fail
        self.one_tick(controller, start=False)
        self.assertEqual(controller.snapshot()['state'], 'ERROR')
        self.assertTrue(controller.closing)
        self.assertEqual(FakePin.levels[14], 0)
        self.assertEqual(controller.fans[1].commands, [])
        self.assertTrue(controller.fans[1].stopped)
        controller.fans[0].on_stop = None

    def test_stop_pressed_during_arm_prevents_remaining_fan_arms(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        controller.fans[0].on_arm = lambda: FakePin.levels.__setitem__(14, 0)
        self.one_tick(controller)
        self.assertEqual(controller.fans[1].arms, 0)
        self.assertFalse(controller.snapshot()['running'])
        self.assertTrue(all(fan.stopped for fan in controller.fans.values()))

    def test_stop_pressed_during_fault_sample_still_stops_healthy_fan(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        controller.fans[0].fault = 'command_underrun'
        controller.fans[0].on_sample = lambda: FakePin.levels.__setitem__(14, 0)
        self.one_tick(controller)
        self.assertEqual(controller.snapshot()['state'], 'STOPPED')
        self.assertEqual(controller.snapshot()['message'], 'STOP button')
        self.assertEqual(controller.fans[1].commands, [])
        self.assertTrue(all(fan.stopped for fan in controller.fans.values()))

    def test_brief_stop_latched_during_power_command_stops_run_next_tick(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        def latch_stop():
            # Model PIO retaining a brief shared STOP after GPIO goes HIGH.
            for fan in controller.fans.values():
                fan.stopped = True
                fan.output = 0
        controller.fans[0].on_set_duty = latch_stop
        self.run_for(controller, 120)
        self.assertEqual(controller.snapshot()['state'], 'STOPPED')
        self.assertIn('emergency stop', controller.snapshot()['message'])
        self.assertFalse(controller.snapshot()['running'])
        self.assertEqual(controller.fans[1].commands, [])
        self.assertEqual(FakePin.levels[14], 1)

    def test_clock_change_stops_every_fan_as_controller_error(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        controller.fans[0].fault = 'clock_changed'
        self.one_tick(controller)
        self.assertEqual(controller.snapshot()['state'], 'ERROR')
        self.assertIn('clock changed', controller.snapshot()['message'])
        self.assertEqual(FakePin.levels[14], 0)
        self.assertTrue(all(fan.stopped for fan in controller.fans.values()))

    def test_worker_exception_stops_outputs_and_publishes_error(self):
        controller = self.controller()

        def fail(*args):
            raise RuntimeError("control failed")

        controller.engine.update = fail
        controller.start_button = ButtonScript(False)
        controller._run()
        self.assertTrue(controller.finished)
        self.assertTrue(controller.fans[0].stopped)
        self.assertEqual(controller.snapshot()["state"], "ERROR")
        self.assertIn("control failed", controller.error)

    def test_thread_launch_failure_remains_closeable(self):
        controller = self.controller()

        def fail(function, args):
            raise RuntimeError("no worker")

        self.thread_module.start_new_thread = fail
        with self.assertRaises(RuntimeError):
            controller.start_thread()
        self.assertFalse(controller.started)
        controller.close()
        self.assertTrue(controller.fans[0].closed)

    def test_heartbeat_grace_starts_when_worker_is_launched(self):
        controller = self.controller()
        self.clock.now += 1500  # Display/radio work before core1 starts.
        controller.start_thread()
        self.assertEqual(controller.heartbeat, self.clock.now)
        controller.finished = True  # The test's thread launcher is a no-op.

    def test_core0_shutdown_asserts_stop_without_touching_live_driver(self):
        controller = self.controller()
        fan = controller.fans[0]
        controller.started = True
        controller.request_shutdown('Heartbeat missed')
        self.assertTrue(controller.closing)
        self.assertEqual(controller.error, 'Heartbeat missed')
        self.assertEqual(FakePin.modes[14], FakePin.OUT)
        self.assertEqual(FakePin.levels[14], 0)
        self.assertEqual(fan.stops, 0)
        self.assertFalse(fan.closed)
        controller.finished = True  # Permit tearDown cleanup after acknowledgement.

    def test_shutdown_during_arm_prevents_engine_start_and_second_fan_arm(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        controller.fans[0].on_arm = controller.request_shutdown
        controller.start_button = ButtonScript()
        with patch.object(controller.engine, 'start', wraps=controller.engine.start) as start:
            controller._run()
        self.assertEqual(start.call_count, 0)
        self.assertEqual(controller.fans[1].arms, 0)
        self.assertTrue(all(fan.stopped for fan in controller.fans.values()))
        self.assertEqual(FakePin.levels[14], 0)
        self.assertFalse(controller._running)

    def test_shutdown_during_engine_start_prevents_any_power_command(self):
        controller = self.controller()
        original = controller.engine.start

        def request_after_start(*args):
            result = original(*args)
            controller.request_shutdown()
            return result

        controller.engine.start = request_after_start
        controller.start_button = ButtonScript()
        controller._run()
        self.assertEqual(controller.fans[0].commands, [])
        self.assertFalse(controller.engine.running)
        self.assertTrue(controller.fans[0].stopped)

    def test_unacknowledged_worker_keeps_stop_asserted_and_resources_untouched(self):
        controller = self.controller()
        controller.started = True
        fan = controller.fans[0]
        with self.assertRaisesRegex(RuntimeError, 'hardware STOP held LOW'):
            controller.close()
        self.assertEqual(self.clock.now, 2000)
        self.assertEqual(fan.stops, 0)
        self.assertFalse(fan.closed)
        self.assertEqual(FakePin.modes[14], FakePin.OUT)
        self.assertEqual(FakePin.levels[14], 0)
        controller.finished = True
        controller.close()
        self.assertTrue(fan.closed)
        self.assertEqual(FakePin.modes[14], FakePin.IN)

    def test_stop_is_released_only_after_all_acknowledged_outputs_close(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        controller.started = controller.finished = True
        observed = []
        for fan in controller.fans.values():
            fan.on_close = lambda: observed.append((FakePin.modes[14], FakePin.levels[14]))
        controller.close()
        self.assertEqual(observed, [(FakePin.OUT, 0)] * 4)
        self.assertTrue(all(fan.closed for fan in controller.fans.values()))
        self.assertEqual(FakePin.modes[14], FakePin.IN)

    def test_failed_close_keeps_stop_asserted_and_attempts_remaining_outputs(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        def fail():
            raise RuntimeError('close failure')
        controller.fans[0].on_close = fail
        with self.assertRaisesRegex(RuntimeError, 'close failure'):
            controller.close()
        self.assertTrue(controller.fans[1].closed)
        self.assertEqual(FakePin.modes[14], FakePin.OUT)
        self.assertEqual(FakePin.levels[14], 0)
        controller.fans[0].on_close = None

    def test_disabled_fans_are_not_sampled_commanded_or_used_for_driver_errors(self):
        controller = self.controller()
        controller.fans[1].fault = 'command_underrun'
        self.one_tick(controller)
        self.assertNotEqual(controller.snapshot()['state'], 'ERROR')
        self.assertIsNone(controller.snapshot()['fans'][1]['driver_error'])
        for i in (1, 2, 3):
            self.assertEqual(controller.fans[i].arms, 0)
            self.assertEqual(controller.fans[i].samples, 0)
            self.assertEqual(controller.fans[i].commands, [])

    def test_contested_channels_are_never_constructed_or_enabled(self):
        self.config.ENABLED_CHANNELS = (0, 4, 5)
        controller = self.controller()
        self.assertEqual(controller.enabled, (0,))
        self.assertEqual([fan.pwm for fan in FakeFan.all], [18, 20, 22, 1])
        for i in (4, 5):
            with self.assertRaisesRegex(ValueError, 'disconnect the kit links'):
                controller.set_fan_enabled(i, True)
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    def test_aux_flag_makes_all_six_available_without_enabling_them(self):
        self.config.AUX_LINKS_DISCONNECTED = True
        controller = self.controller()
        self.assertEqual(tuple(controller.fans), tuple(range(6)))
        self.assertEqual(controller.enabled, (0,))
        state = controller.set_fan_enabled(4, True)
        self.assertTrue(state['fans'][4]['enabled'])
        self.assertEqual(controller.fans[4].arms, 0)
        self.assertTrue(controller.fans[4].stopped)

    def test_all_off_is_persisted_and_start_refused_before_any_arm(self):
        controller = self.controller()
        state = controller.set_fan_enabled(0, False)
        self.assertFalse(any(f['enabled'] for f in state['fans']))
        self.assertEqual(controller.fan_settings.load(), ())
        self.one_tick(controller)
        self.assertTrue(all(fan.arms == 0 for fan in controller.fans.values()))
        self.assertIn('Enable at least one fan', controller.snapshot()['message'])
        self.assertFalse(controller.engine.running)

    def test_toggle_does_not_rearm_stopped_output(self):
        controller = self.controller()
        controller.engine.stop('STOP button')
        controller._stop_outputs()
        controller.set_fan_enabled(1, True)
        self.assertEqual(controller.enabled, (0, 1))
        self.assertTrue(all(fan.arms == 0 for fan in controller.fans.values()))
        self.assertTrue(all(fan.stopped for fan in controller.fans.values()))

    def test_selection_refused_during_running_and_start_claim(self):
        controller = self.controller()
        original = controller.enabled
        observed = []
        def try_toggle():
            with self.assertRaisesRegex(RuntimeError, 'Stop the recipe'):
                controller.set_fan_enabled(1, True)
            observed.append(controller.enabled)
        controller.fans[0].on_arm = try_toggle
        self.one_tick(controller)
        self.assertEqual(observed, [original])
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    def test_save_failure_preserves_live_and_published_selection(self):
        controller = self.controller()
        with patch.object(controller.fan_settings, 'save', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                controller.set_fan_enabled(0, False)
        self.assertEqual(controller.enabled, (0,))
        self.assertTrue(controller.snapshot()['fans'][0]['enabled'])
        self.assertFalse(controller.editing)
        self.assertTrue(all(fan.stopped for fan in controller.fans.values()))

    def test_uncertain_save_asserts_hardware_stop(self):
        controller = self.controller()
        controller.fan_settings.uncertain = True
        with patch.object(controller.fan_settings, 'save', side_effect=OSError('flash failure')):
            with self.assertRaises(OSError):
                controller.set_fan_enabled(0, False)
        self.assertTrue(controller.closing)
        self.assertEqual(FakePin.levels[14], 0)

    def test_live_worker_ack_precedes_flash_and_selection_publishes_before_return(self):
        controller = self.controller()
        controller.started = True
        phases = []
        original_save = controller.fan_settings.save
        def save(candidate):
            self.assertTrue(controller._edit_ready)
            self.assertTrue(all(fan.stopped for fan in controller.fans.values()))
            self.assertEqual(controller.enabled, (0,))
            phases.append('save')
            return original_save(candidate)
        def acknowledge():
            phases.append('apply' if controller._pending_enabled is not None else 'pause')
            controller._service_edit()
        controller.fan_settings.save = save
        self.clock.on_sleep = acknowledge
        try:
            state = controller.set_fan_enabled(1, True)
        finally:
            self.clock.on_sleep = None
            controller.finished = True
        self.assertEqual(phases, ['pause', 'save', 'apply'])
        self.assertEqual(controller.enabled, (0, 1))
        self.assertTrue(state['fans'][1]['enabled'])
        self.assertFalse(controller.editing)
        self.assertEqual(controller.fans[1].arms, 0)

    def test_missing_pause_ack_stops_without_writing_or_core0_driver_calls(self):
        controller = self.controller()
        controller.started = True
        stops = [fan.stops for fan in controller.fans.values()]
        with patch.object(controller.fan_settings, 'save') as save:
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement failed'):
                controller.set_fan_enabled(1, True)
        self.assertEqual(save.call_count, 0)
        self.assertEqual([fan.stops for fan in controller.fans.values()], stops)
        self.assertEqual(FakePin.levels[14], 0)
        self.assertTrue(controller.closing)
        controller.finished = True

    def test_missing_apply_ack_stops_after_disk_save_without_live_change(self):
        controller = self.controller()
        controller.started = True
        self.clock.on_sleep = lambda: setattr(controller, '_edit_ready', True)
        try:
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement failed'):
                controller.set_fan_enabled(1, True)
        finally:
            self.clock.on_sleep = None
            controller.finished = True
        self.assertEqual(controller.enabled, (0,))
        self.assertEqual(controller.fan_settings.load(), (0, 1))
        self.assertTrue(controller.closing)
        self.assertEqual(FakePin.levels[14], 0)

    def test_editing_worker_services_stop_and_heartbeat_and_consumes_start(self):
        controller = self.controller()
        controller.editing = True
        FakePin.levels[14] = 0
        self.clock.now = 25
        self.one_tick(controller)
        self.assertEqual(controller.engine.message, 'STOP button')
        self.assertEqual(controller.heartbeat, 25)
        self.assertEqual(controller.fans[0].arms, 0)
        self.assertTrue(controller._edit_ready)

    def test_failed_output_stop_never_acknowledges_or_saves_edit(self):
        controller = self.controller()
        with patch.object(controller.fans[0], 'stop', side_effect=OSError('driver stop failed')), \
                patch.object(controller.fan_settings, 'save') as save:
            with self.assertRaisesRegex(RuntimeError, 'Could not stop outputs'):
                controller.set_fan_enabled(0, False)
        self.assertEqual(save.call_count, 0)
        self.assertFalse(controller._edit_ready)
        self.assertFalse(controller.editing)
        self.assertTrue(controller.closing)
        self.assertEqual(FakePin.levels[14], 0)

    def test_start_begun_during_edit_does_not_mature_after_edit_finishes(self):
        controller = self.controller()
        button = controller.start_button  # Exercise real debounce, not ButtonScript.
        controller._begin_edit()
        FakePin.levels[15] = 0
        self.assertFalse(button.pressed(10))
        controller._end_edit()
        self.assertFalse(button.pressed(50))
        self.assertFalse(button.pressed(500))
        FakePin.levels[15] = 1
        self.assertFalse(button.pressed(510))
        self.assertFalse(button.pressed(550))
        FakePin.levels[15] = 0
        self.assertFalse(button.pressed(560))
        self.assertTrue(button.pressed(600))


if __name__ == "__main__":
    unittest.main()
