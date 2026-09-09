"""Touch target boundaries, disabled visibility, and actionable fan labels."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'device'))
from dashboard import Dashboard, BG, CARD, CYAN, DISABLED_CARD, WARNING_CARD, TRACK, WHITE, RED


class Display:
    def __init__(self):
        self.calls = []

    def fill(self, color):
        self.fill_rect(0, 0, 480, 320, color)

    def fill_rect(self, x, y, width, height, color):
        assert 0 <= x < x + width <= 480 and 0 <= y < y + height <= 320
        self.calls.append(('rect', x, y, width, height, color))

    def hline(self, x, y, width, color):
        self.fill_rect(x, y, width, 1, color)

    def text(self, text, x, y, color, scale=1, bg=0):
        assert x >= 0 and x + len(str(text)) * 8 * scale <= 480
        assert y >= 0 and y + 8 * scale <= 320
        self.calls.append(('text', str(text), x, y, color, bg))


class DashboardToggleTests(unittest.TestCase):
    def setUp(self):
        self.display = Display()
        self.dashboard = Dashboard(self.display)

    def labels(self):
        return [call[1] for call in self.display.calls if call[0] == 'text']

    def test_six_buttons_have_disjoint_targets_and_rpm_is_not_a_button(self):
        for channel in range(6):
            x, y = 8 + channel % 3 * 156, 76 + channel // 3 * 84
            for px, py in ((x + 76, y), (x + 149, y + 31), (x + 112, y + 16)):
                self.assertEqual(self.dashboard.hit_test(px, py), channel)
            for px, py in ((x + 75, y + 16), (x + 150, y + 16),
                           (x + 112, y + 32), (x + 12, y + 45)):
                self.assertIsNone(self.dashboard.hit_test(px, py))
        for point in ((0, 0), (479, 319), (100, 249), (100, 291), (480, 320)):
            self.assertIsNone(self.dashboard.hit_test(*point))

    def test_disabled_card_is_red_and_white_with_enable_action(self):
        self.dashboard.update({'fans': [{'enabled': False, 'available': True,
                                          'valid': True, 'rpm': 3000, 'duty': 70}]})
        self.assertIn(('rect', 8, 76, 152, 80, DISABLED_CARD), self.display.calls)
        self.assertIn('ENABLE', self.labels())
        self.assertIn('PWM   0%', self.labels())
        disabled_text = [call for call in self.display.calls if call[0] == 'text'
                         and call[5] == DISABLED_CARD]
        self.assertTrue(disabled_text)
        self.assertTrue(all(call[4] == WHITE for call in disabled_text))
        self.assertNotIn('3000', self.labels())

    def test_enabling_and_disabling_repaints_card_and_stale_numbers(self):
        snapshot = {'fans': [{'enabled': False}]}
        self.dashboard.update(snapshot)
        self.display.calls.clear()
        snapshot['fans'][0].update(enabled=True, valid=True, rpm=2450, duty=55)
        self.dashboard.update(snapshot)
        self.assertIn(('rect', 8, 76, 152, 80, CARD), self.display.calls)
        self.assertIn('DISABLE', self.labels())
        self.assertIn('2450', self.labels())
        self.display.calls.clear()
        snapshot['fans'][0]['enabled'] = False
        self.dashboard.update(snapshot)
        self.assertIn(('rect', 8, 76, 152, 80, DISABLED_CARD), self.display.calls)
        self.assertIn('ENABLE', self.labels())
        self.assertNotIn('2450', self.labels())

    def test_running_and_gpio_lock_replace_actions_without_full_redraw(self):
        snapshot = {'fans': [{'enabled': True}, {'enabled': False},
                             {'enabled': False, 'available': False}]}
        self.dashboard.update(snapshot)
        self.assertIn('DISABLE', self.labels())
        self.assertIn('ENABLE', self.labels())
        self.assertIn('LOCKED', self.labels())
        self.display.calls.clear()
        snapshot['running'] = True
        self.dashboard.update(snapshot)
        self.assertIn('RUN', self.labels())
        self.assertIn('OFF', self.labels())
        self.assertNotIn('ENABLE', self.labels())
        self.assertNotIn('DISABLE', self.labels())
        self.assertFalse(any(call[0] == 'rect' and call[3:5] == (152, 80)
                             for call in self.display.calls))
        self.assertEqual(self.dashboard.hit_test(432, 92), 2)

    def test_notice_can_be_cleared_but_cannot_hide_controller_error(self):
        snapshot = {'message': 'Ready'}
        self.dashboard.set_notice('Stop recipe before changing fans')
        self.dashboard.update(snapshot)
        self.assertIn('Stop recipe before changing fans', self.labels())
        self.display.calls.clear()
        self.dashboard.set_notice(None)
        self.dashboard.update(snapshot)
        self.assertIn('Ready', self.labels())
        self.display.calls.clear()
        self.dashboard.set_notice('Old notice')
        self.dashboard.update({'state': 'ERROR', 'message': 'Controller error'})
        self.assertIn('Controller error', self.labels())
        self.assertNotIn('Old notice', self.labels())

    def test_warning_shows_actual_power_and_clears_during_same_run(self):
        snapshot = {'state': 'DWELL', 'running': True, 'fans': [
            {'enabled': True, 'valid': True, 'rpm': 3000, 'duty': 70},
            {'enabled': True, 'valid': True, 'rpm': 2998, 'duty': 69}]}
        self.dashboard.update(snapshot)
        self.display.calls.clear()
        snapshot['fans'][0].update(warning=True, in_bounds=False, rpm=2200,
                                   error_percent=26.7, out_of_bounds_s=2.1, duty=75)
        self.dashboard.update(snapshot)
        self.assertIn(('rect', 8, 76, 152, 80, WARNING_CARD), self.display.calls)
        self.assertIn('OFF RPM', self.labels())
        self.assertIn('2200', self.labels())
        self.assertIn('PWM  75%', self.labels())
        self.assertFalse(any(call[0] == 'rect' and call[1:5] == (164, 76, 152, 80)
                             for call in self.display.calls))
        self.assertIn(('rect', 16, 147, 136, 5, TRACK), self.display.calls)
        self.assertIn(('rect', 16, 147, 102, 5, RED), self.display.calls)
        calls = len(self.display.calls)
        self.dashboard.update(snapshot)
        self.assertEqual(len(self.display.calls), calls)

        # RPM recovery repaints immediately, without stopping or restarting.
        self.display.calls.clear()
        snapshot['fans'][0].update(warning=False, in_bounds=True, rpm=2980,
                                   error_percent=0.67, out_of_bounds_s=0, duty=74)
        self.dashboard.update(snapshot)
        self.assertIn(('rect', 8, 76, 152, 80, CARD), self.display.calls)
        self.assertIn('RUN', self.labels())
        self.assertIn('2980', self.labels())
        self.assertIn('PWM  74%', self.labels())
        self.assertIn(('rect', 16, 147, 101, 5, CYAN), self.display.calls)
        self.assertNotIn('OFF RPM', self.labels())

    def test_outside_band_before_delay_is_normal_and_disabled_warning_is_ignored(self):
        self.dashboard.update({'state': 'DWELL', 'running': True, 'fans': [
            {'enabled': True, 'warning': False, 'in_bounds': False,
             'out_of_bounds_s': 1.0, 'valid': True, 'rpm': 1800, 'duty': 80},
            {'enabled': False, 'warning': True, 'valid': True, 'rpm': 1800, 'duty': 80}]})
        self.assertIn(('rect', 8, 76, 152, 80, CARD), self.display.calls)
        self.assertIn(('rect', 164, 76, 152, 80, DISABLED_CARD), self.display.calls)
        self.assertNotIn('OFF RPM', self.labels())
        self.assertIn('PWM  80%', self.labels())
        self.assertIn('PWM   0%', self.labels())

    def test_missing_reading_warning_does_not_hide_power_or_idle_disable_action(self):
        snapshot = {'state': 'DWELL', 'running': True, 'fans': [
            {'enabled': True, 'warning': True, 'valid': False, 'duty': 30}]}
        self.dashboard.update(snapshot)
        self.assertIn('--', self.labels())
        self.assertIn('PWM  30%', self.labels())
        self.assertIn(('rect', 16, 147, 41, 5, RED), self.display.calls)
        self.display.calls.clear()
        snapshot.update(state='STOPPED', running=False)
        self.dashboard.update(snapshot)
        self.assertIn(('text', 'DISABLE', 92, 89, WHITE, TRACK), self.display.calls)

    def test_global_error_does_not_invent_individual_rpm_warnings(self):
        self.dashboard.update({'state': 'ERROR', 'message': 'Controller error',
                               'fans': [{'enabled': True, 'warning': False}]})
        self.assertIn(('text', 'ERROR', 8, 9, RED, BG), self.display.calls)
        self.assertIn(('rect', 8, 76, 152, 80, CARD), self.display.calls)
        self.assertNotIn('OFF RPM', self.labels())

    def test_driver_error_labels_channel_without_turning_it_red(self):
        snapshot = {'state': 'DWELL', 'running': True, 'fans': [
            {'enabled': True, 'warning': False, 'driver_error': 'command_underrun',
             'valid': False, 'duty': 0}]}
        self.dashboard.update(snapshot)
        self.assertIn('IO ERROR', self.labels())
        self.assertIn(('rect', 8, 76, 152, 80, CARD), self.display.calls)
        self.assertNotIn(('rect', 8, 76, 152, 80, WARNING_CARD), self.display.calls)
        self.display.calls.clear()
        snapshot['fans'][0].update(driver_error=None, warning=False, duty=30)
        self.dashboard.update(snapshot)
        self.assertIn('RUN', self.labels())
        self.assertIn('PWM  30%', self.labels())
        self.assertNotIn('IO ERROR', self.labels())


if __name__ == '__main__':
    unittest.main()
