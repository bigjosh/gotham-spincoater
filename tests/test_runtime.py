"""Control-core lifecycle and deterministic START/edit race checks."""
import importlib.util
from pathlib import Path
import sys
import threading
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
        self.arm_result = True
        self.fault = None
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
        if self.on_sample:
            self.on_sample()
        return {"rpm": 0, "valid": False, "pulses": 0,
                "stopped": self.stopped, "fault": self.fault}

    def set_duty(self, value):
        self.commands.append(value)
        self.output = value

    def stop(self):
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
        config.FUTURE_CHANNELS = ((18, 19), (20, 21), (22, 28), (0, 1), (12, 16), (13, 17))
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

    def tearDown(self):
        for controller in self.controllers:
            controller.close()
        self.paths.stop()
        self.modules.stop()

    def controller(self):
        controller = self.runtime.Controller(self.book)
        self.controllers.append(controller)
        return controller

    def one_tick(self, controller, start=True):
        controller.start_button = ButtonScript(start)
        self.clock.on_sleep = lambda: setattr(controller, "closing", True)
        try:
            controller._run()
        finally:
            self.clock.on_sleep = None

    def changed_data(self):
        data = self.validate_document(self.book.data)
        data["settings"]["max_power_per_s"] = 7
        return data

    def test_constructor_allocates_only_enabled_fans_at_zero(self):
        controller = self.controller()
        self.assertEqual(list(controller.fans), [0])
        self.assertEqual(controller.fans[0].output, 0)
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

    def test_hardware_fault_is_published_and_every_output_stopped(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        controller = self.controller()
        controller.fans[1].fault = "command_underrun"
        self.one_tick(controller, start=False)
        self.assertEqual(controller.snapshot()["state"], "FAULT")
        self.assertIn("command_underrun", controller.snapshot()["message"])
        self.assertTrue(all(fan.stopped for fan in controller.fans.values()))

    def test_failed_arm_does_not_start_engine(self):
        controller = self.controller()
        controller.fans[0].arm_result = False
        self.one_tick(controller)
        self.assertFalse(controller.engine.running)
        self.assertEqual(controller.fans[0].output, 0)

    def test_worker_exception_stops_outputs_and_publishes_fault(self):
        controller = self.controller()

        def fail():
            raise RuntimeError("sample failed")

        controller.fans[0].on_sample = fail
        controller.start_button = ButtonScript(False)
        controller._run()
        self.assertTrue(controller.finished)
        self.assertTrue(controller.fans[0].stopped)
        self.assertEqual(controller.snapshot()["state"], "FAULT")
        self.assertIn("sample failed", controller.error)

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
        self.assertEqual(observed, [(FakePin.OUT, 0), (FakePin.OUT, 0)])
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


if __name__ == "__main__":
    unittest.main()
