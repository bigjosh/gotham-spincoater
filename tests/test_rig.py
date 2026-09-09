"""Validate channel allocation and lifecycle without opening any hardware."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


DEVICE_DIR = Path(__file__).resolve().parents[1] / "device"


def load_file(name, filename):
    spec = importlib.util.spec_from_file_location(name, DEVICE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RigTests(unittest.TestCase):
    def setUp(self):
        self.config = load_file("rig_test_config", "config.py")
        self.constructed = []
        self.attempted = []
        self.fail_on_pwm = None
        owner = self

        class FanStub:
            def __init__(self, pwm, tach, ppr, **kwargs):
                owner.attempted.append((pwm, tach, ppr, kwargs))
                if pwm == owner.fail_on_pwm:
                    raise RuntimeError("simulated PIO allocation failure")
                self.pwm, self.tach, self.ppr = pwm, tach, ppr
                self.kwargs = kwargs
                self.commands = []
                self.close_count = 0
                self.reading = {"rpm": float(pwm * 100), "hz": pwm,
                                "valid": True, "duty": 0}
                owner.constructed.append(self)

            def set_duty(self, duty):
                self.commands.append(duty)
                self.reading["duty"] = duty

            def sample(self):
                return self.reading

            def close(self):
                self.close_count += 1

        fake_fan = types.ModuleType("fan")
        fake_fan.Fan = FanStub
        with patch.dict(sys.modules, {"config": self.config, "fan": fake_fan}):
            self.module = load_file("rig_under_test", "rig.py")

    def assert_rejected_before_construction(self):
        with self.assertRaises(ValueError):
            self.module.FanRig()
        self.assertEqual(self.attempted, [])

    def test_shipped_configuration_unlocks_auxiliary_channels_without_selecting_them(self):
        self.assertEqual(self.config.ENABLED_CHANNELS, (0, 1, 2, 3))
        self.assertTrue(self.config.AUX_LINKS_DISCONNECTED)
        rig = self.module.FanRig()
        self.assertEqual(list(rig.fans), [0, 1, 2, 3])
        self.assertEqual([(fan.pwm, fan.tach) for fan in self.constructed],
                         [(18, 19), (20, 21), (22, 28), (1, 0)])

    def test_pwm_is_immediately_below_tach_except_fan_two(self):
        # Physical Pico header positions, independent of GPIO numbering:
        # left pins count down 1..20; right pins count down 40..21.
        physical = {0: 1, 1: 2, 12: 16, 13: 17, 16: 21, 17: 22,
                    18: 24, 19: 25, 20: 26, 21: 27, 22: 29, 28: 34}

        def position(gpio):
            pin = physical[gpio]
            return (0, pin) if pin <= 20 else (1, 41 - pin)

        self.assertEqual(self.config.FUTURE_CHANNELS[2], (22, 28))
        for channel, (pwm, tach) in enumerate(self.config.FUTURE_CHANNELS):
            if channel == 2:
                continue
            with self.subTest(channel=channel):
                pwm_side, pwm_row = position(pwm)
                tach_side, tach_row = position(tach)
                self.assertEqual(pwm_side, tach_side)
                self.assertEqual(pwm_row, tach_row + 1)

    def test_rejects_empty_duplicate_or_missing_primary_channels(self):
        for ids in ((), (1,), (0, 0), (0, 1, 1)):
            with self.subTest(ids=ids):
                self.config.ENABLED_CHANNELS = ids
                self.assert_rejected_before_construction()

    def test_rejects_out_of_range_and_noninteger_channels(self):
        for ids in ((0, -1), (0, 6), (0, "1"), (False,), (0, 1.0)):
            with self.subTest(ids=ids):
                self.config.ENABLED_CHANNELS = ids
                self.assert_rejected_before_construction()

    def test_channels_four_and_five_require_auxiliary_links_disconnected(self):
        self.config.AUX_LINKS_DISCONNECTED = False
        for ids in ((0, 4), (0, 5), (0, 4, 5)):
            with self.subTest(ids=ids):
                self.config.ENABLED_CHANNELS = ids
                self.assert_rejected_before_construction()

    def test_first_four_channels_do_not_require_auxiliary_link_changes(self):
        self.config.AUX_LINKS_DISCONNECTED = False
        self.config.ENABLED_CHANNELS = (0, 1, 2, 3)
        rig = self.module.FanRig()
        self.assertEqual(list(rig.fans), [0, 1, 2, 3])
        self.assertEqual(len(self.constructed), 4)

    def test_all_six_channels_have_distinct_gpio_and_state_machine_allocations(self):
        self.config.ENABLED_CHANNELS = tuple(range(6))
        self.config.AUX_LINKS_DISCONNECTED = True
        rig = self.module.FanRig()
        self.assertEqual(list(rig.fans), list(range(6)))
        pins = [pin for fan in self.constructed for pin in (fan.pwm, fan.tach)]
        sms = [fan.kwargs["tach_sm_id"] for fan in self.constructed]
        self.assertEqual(len(set(pins)), 12)
        self.assertEqual(len(set(sms)), 6)
        for channel, fan in rig.fans.items():
            self.assertEqual((fan.pwm, fan.tach), self.config.FUTURE_CHANNELS[channel])
            self.assertEqual(fan.ppr, self.config.PULSES_PER_REV)
            self.assertEqual(fan.kwargs, {
                "push_pull": self.config.PWM_PUSH_PULL,
                "pwm_hz": self.config.PWM_HZ,
                "synchronous": self.config.SYNCHRONOUS_TACH,
                "tach_sm_id": self.config.TACH_STATE_MACHINES[channel],
                "period_window": self.config.PERIOD_AVERAGE,
            })

    def test_rejects_gpio_collision_within_or_between_channels(self):
        for channels in (((18, 18), (20, 21)), ((18, 19), (20, 18))):
            with self.subTest(channels=channels):
                self.config.FUTURE_CHANNELS = channels
                self.config.ENABLED_CHANNELS = (0, 1)
                self.assert_rejected_before_construction()

    def test_rejects_shared_pio_state_machine(self):
        self.config.ENABLED_CHANNELS = (0, 1)
        self.config.TACH_STATE_MACHINES = (0, 0, 2, 3, 8, 9)
        self.assert_rejected_before_construction()

    def test_failed_construction_closes_previously_created_fans(self):
        self.config.ENABLED_CHANNELS = (0, 1, 2, 3)
        self.fail_on_pwm = self.config.FUTURE_CHANNELS[2][0]
        with self.assertRaisesRegex(RuntimeError, "PIO allocation failure"):
            self.module.FanRig()
        self.assertEqual(len(self.attempted), 3)
        self.assertEqual(len(self.constructed), 2)
        self.assertEqual([fan.close_count for fan in self.constructed], [1, 1])
        self.assertEqual([fan.commands for fan in self.constructed], [[], []])

    def test_shared_manual_command_reaches_every_enabled_fan(self):
        self.config.ENABLED_CHANNELS = (0, 2, 3)
        rig = self.module.FanRig()
        rig.set_duty(37.5)
        rig.set_duty(0)
        self.assertEqual([fan.commands for fan in self.constructed],
                         [[37.5, 0], [37.5, 0], [37.5, 0]])

    def test_readings_map_keeps_channel_zero_as_display_primary(self):
        self.config.ENABLED_CHANNELS = (2, 0, 1)
        rig = self.module.FanRig()
        reading = rig.sample()
        self.assertEqual(set(reading["channels"]), {0, 1, 2})
        self.assertEqual(reading["rpm"], rig.fans[0].reading["rpm"])
        for channel, fan in rig.fans.items():
            self.assertEqual(reading["channels"][channel], fan.reading)
        self.assertNotIn("channels", rig.fans[0].reading)
        reading["rpm"] = -1
        self.assertNotEqual(rig.fans[0].reading["rpm"], -1)

    def test_close_reaches_every_constructed_fan(self):
        self.config.ENABLED_CHANNELS = (0, 1, 2, 3)
        rig = self.module.FanRig()
        rig.close()
        self.assertEqual([fan.close_count for fan in self.constructed], [1] * 4)


if __name__ == "__main__":
    unittest.main()
