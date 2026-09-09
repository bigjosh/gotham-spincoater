"""Touch target boundaries, disabled visibility, and actionable fan labels."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'device'))
from dashboard import Dashboard, BG, CARD, DISABLED_CARD, FAULT_CARD, TRACK, WHITE, RED


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
        self.assertNotIn('ENABLE', self.labels())
        self.assertNotIn('DISABLE', self.labels())
        self.assertFalse(any(call[0] == 'rect' and call[3:5] == (152, 80)
                             for call in self.display.calls))
        self.assertEqual(self.dashboard.hit_test(432, 92), 2)

    def test_notice_can_be_cleared_but_cannot_hide_fault(self):
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
        self.dashboard.update({'state': 'FAULT', 'message': 'Fan 0 tach missing'})
        self.assertIn('Fan 0 tach missing', self.labels())
        self.assertNotIn('Old notice', self.labels())

    def test_fault_repaints_only_failed_card_and_keeps_selection_action(self):
        snapshot = {'state': 'DWELL', 'running': True, 'fans': [
            {'enabled': True, 'valid': True, 'rpm': 3000, 'duty': 70},
            {'enabled': True, 'valid': True, 'rpm': 2998, 'duty': 69}]}
        self.dashboard.update(snapshot)
        self.display.calls.clear()
        snapshot['fans'][0].update(fault='tach signal missing', participating=False,
                                   valid=False, duty=0)
        self.dashboard.update(snapshot)
        self.assertIn(('rect', 8, 76, 152, 80, FAULT_CARD), self.display.calls)
        self.assertIn('FAULT', self.labels())
        self.assertIn('--', self.labels())
        self.assertFalse(any(call[0] == 'rect' and call[1:5] == (164, 76, 152, 80)
                             for call in self.display.calls))
        self.assertIn(('rect', 16, 147, 136, 5, TRACK), self.display.calls)
        calls = len(self.display.calls)
        self.dashboard.update(snapshot)
        self.assertEqual(len(self.display.calls), calls)

        # A latched fault does not silently disable the selected fan. The idle
        # button can still disable it, or START can retry that selection.
        self.display.calls.clear()
        snapshot.update(state='COMPLETE', running=False)
        self.dashboard.update(snapshot)
        self.assertIn(('text', 'DISABLE', 92, 89, WHITE, TRACK), self.display.calls)
        self.assertFalse(any(call[0] == 'text' and call[1] == 'ENABLE'
                             and 8 <= call[2] < 160 and 76 <= call[3] < 156
                             for call in self.display.calls))
        self.display.calls.clear()
        snapshot.update(state='RAMP', running=True)
        snapshot['fans'][0].update(fault=None, participating=True)
        self.dashboard.update(snapshot)
        self.assertIn(('rect', 8, 76, 152, 80, CARD), self.display.calls)
        self.assertNotIn('FAULT', self.labels())

    def test_global_error_does_not_invent_individual_fan_faults(self):
        self.dashboard.update({'state': 'FAULT', 'message': 'Controller error',
                               'fans': [{'enabled': True, 'fault': None}]})
        self.assertIn(('text', 'FAULT', 8, 9, RED, BG), self.display.calls)
        self.assertIn(('rect', 8, 76, 152, 80, CARD), self.display.calls)
        self.assertFalse(any(call[0] == 'text' and call[1] == 'FAULT' and call[3] >= 76
                             for call in self.display.calls))


if __name__ == '__main__':
    unittest.main()
