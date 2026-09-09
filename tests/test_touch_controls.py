"""Touch-button routing through controller validation, without GPIO access."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


DEVICE = Path(__file__).resolve().parents[1] / 'device'


def load(name):
    spec = importlib.util.spec_from_file_location('_test_' + name, DEVICE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controls = load('touch_controls')
dashboard = load('dashboard')


class TouchStub:
    def __init__(self):
        self.points = []
        self.reads = 0

    def poll(self):
        self.reads += 1
        item = self.points.pop(0) if self.points else None
        if isinstance(item, Exception):
            raise item
        return item


class DashboardStub:
    hit_test = staticmethod(dashboard.Dashboard.hit_test)

    def __init__(self):
        self.notice = None

    def set_notice(self, text):
        self.notice = text


class ControllerStub:
    def __init__(self):
        self.state = {'running': False, 'fans': [
            {'enabled': i < 4, 'available': i < 4} for i in range(6)]}
        self.changes = []
        self.error = None

    def snapshot(self):
        return self.state

    def set_fan_enabled(self, channel, enabled):
        if self.error:
            raise self.error
        self.changes.append((channel, enabled))
        self.state['fans'][channel]['enabled'] = enabled


class FanTouchControlsTests(unittest.TestCase):
    def setUp(self):
        self.touch = TouchStub()
        self.dashboard = DashboardStub()
        self.controller = ControllerStub()
        self.controls = controls.FanTouchControls(self.touch, self.dashboard, self.controller)
        self.prints = patch('builtins.print')
        self.prints.start()
        self.addCleanup(self.prints.stop)

    def tap(self, channel, now):
        self.touch.points.append((120 + (channel % 3) * 156,
                                  90 + (channel // 3) * 84))
        self.controls.poll(now)

    def test_same_button_disables_then_enables_from_current_snapshot(self):
        self.tap(1, 0)
        self.assertEqual(self.controller.changes, [(1, False)])
        self.assertIn('disabled', self.dashboard.notice)
        self.tap(1, 40)
        self.assertEqual(self.controller.changes, [(1, False), (1, True)])
        self.assertIn('enabled', self.dashboard.notice)

    def test_locked_and_running_buttons_never_write_settings(self):
        self.tap(4, 0)
        self.assertIn('GPIO', self.dashboard.notice)
        self.controller.state['running'] = True
        self.tap(0, 40)
        self.assertIn('Stop the recipe', self.dashboard.notice)
        self.assertEqual(self.controller.changes, [])

    def test_taps_outside_buttons_do_nothing(self):
        self.touch.points = [(40, 120), (470, 310)]
        self.controls.poll(0)
        self.controls.poll(40)
        self.assertEqual(self.controller.changes, [])
        self.assertIsNone(self.dashboard.notice)

    def test_start_race_or_save_failure_is_reported_without_success_notice(self):
        for error in (RuntimeError('Stop the recipe before editing'),
                      OSError('Storage full')):
            self.controller.error = error
            self.tap(0, self.touch.reads * 40)
            self.assertEqual(self.dashboard.notice, str(error))
            self.assertTrue(self.controller.state['fans'][0]['enabled'])
        self.assertEqual(self.controller.changes, [])

    def test_i2c_error_does_not_toggle_and_later_fresh_tap_works(self):
        self.touch.points = [OSError('I2C timeout')]
        self.controls.poll(0)
        self.assertEqual(self.controls.error, 'I2C timeout')
        self.assertEqual(self.controller.changes, [])
        self.controls.poll(40)
        self.assertIsNone(self.controls.error)
        self.tap(2, 80)
        self.assertEqual(self.controller.changes, [(2, False)])

    def test_driver_reported_error_stays_visible_during_retry_backoff(self):
        self.touch.last_error = 'Controller read timed out'
        self.controls.poll(0)
        self.controls.poll(20)
        self.assertEqual(self.controls.error, self.touch.last_error)
        self.assertIn('Touch read failed', self.dashboard.notice)
        self.assertEqual(self.controller.changes, [])
        self.touch.last_error = None
        self.controls.poll(300)
        self.assertIsNone(self.controls.error)

    def test_poll_rate_and_notice_expiry_across_tick_wrap(self):
        end = (1 << 30) - 10
        self.tap(0, end)
        self.controls.poll(0)
        self.assertEqual(self.touch.reads, 1)
        self.controls.poll(10)
        self.assertEqual(self.touch.reads, 2)
        self.controls.poll(3990)
        self.assertIsNone(self.dashboard.notice)


if __name__ == '__main__':
    unittest.main()
