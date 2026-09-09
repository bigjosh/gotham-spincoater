"""GT911 packet, address/reset, orientation and one-press filtering tests."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def packet(x=100, y=200, count=1, ready=True, track=0, flags=0):
    return bytes(((0x80 if ready else 0) | count | flags, track,
                  x & 255, x >> 8, y & 255, y >> 8, 1, 0, 0))


class FakePin:
    IN, OUT = 0, 1
    changes = []

    def __init__(self, number, mode=None, pull=None, value=None):
        self.number = number
        if mode is not None:
            self.init(mode, pull, value)

    def init(self, mode, pull=None, value=None):
        self.changes.append((self.number, mode, pull, value))


class FakeI2C:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.info = b"911\x00\x40\x10\x40\x01\xe0\x01"
        self.next = packet(count=0, ready=False)
        self.calls = []
        self.ack_error = None

    def readfrom_mem(self, address, register, count, addrsize):
        self.calls.append(("info", address, register, count, addrsize))
        return self.info

    def readfrom_mem_into(self, address, register, buf, addrsize):
        self.calls.append(("read", address, register, len(buf), addrsize))
        if isinstance(self.next, Exception):
            raise self.next
        buf[:] = self.next

    def writeto_mem(self, address, register, buf, addrsize):
        self.calls.append(("ack", address, register, bytes(buf), addrsize))
        if self.ack_error is not None:
            raise self.ack_error
        self.next = packet(count=0, ready=False)


class TouchTests(unittest.TestCase):
    PERIOD = 1 << 30

    def setUp(self):
        self.now = 0
        self.sleeps = []
        FakePin.changes = []
        machine = types.ModuleType("machine")
        machine.Pin, machine.I2C = FakePin, FakeI2C
        clock = types.ModuleType("time")
        clock.ticks_ms = lambda: self.now % self.PERIOD
        clock.ticks_diff = lambda a, b: (
            (a - b + self.PERIOD // 2) % self.PERIOD - self.PERIOD // 2)
        clock.sleep_ms = self.sleeps.append
        path = Path(__file__).resolve().parents[1] / "device" / "touch.py"
        spec = importlib.util.spec_from_file_location("touch_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"machine": machine, "time": clock}):
            spec.loader.exec_module(self.module)
        self.i2c = FakeI2C()
        self.touch = self.module.Touch(i2c=self.i2c, reset=False)

    def poll(self, report=None, dt=20):
        self.now += dt
        if report is not None:
            self.i2c.next = report
        return self.touch.poll()

    def arm(self):
        self.assertIsNone(self.poll(dt=200))
        self.assertTrue(self.touch.diagnostics()["armed"])

    def release(self):
        self.assertIsNone(self.poll(packet(count=0)))
        self.assertIsNone(self.poll(dt=50))

    def test_controller_identity_and_native_resolution(self):
        info = self.touch.diagnostics()
        self.assertEqual(info["product_id"], "911")
        self.assertEqual(info["raw_resolution"], (320, 480))
        self.assertEqual(info["firmware_version"], 0x1040)
        self.assertEqual((self.touch.width, self.touch.height), (480, 320))

    def test_reset_selects_low_address_then_floating_int(self):
        touch = self.module.Touch()
        self.assertEqual(self.sleeps, [20, 2, 6, 50])
        self.assertEqual(FakePin.changes[-5:], [
            (11, FakePin.IN, None, None), (10, FakePin.OUT, None, 0),
            (11, FakePin.OUT, None, 0), (10, FakePin.OUT, None, 1),
            (11, FakePin.IN, None, None)])
        self.assertEqual(touch.i2c.args, (0,))
        self.assertEqual(touch.i2c.kwargs["sda"].number, 8)
        self.assertEqual(touch.i2c.kwargs["scl"].number, 9)
        self.assertEqual(touch.i2c.kwargs["timeout"], 2000)
        self.assertEqual(touch.i2c.kwargs["freq"], 100000)

    def test_diagnostic_attach_does_not_drive_reset_or_int(self):
        self.assertEqual(FakePin.changes, [(11, FakePin.IN, None, None)])
        self.assertEqual(self.sleeps, [])

    def test_bad_identity_resolution_or_rotation_fail_closed(self):
        for info in (b"", b"927\x00\x40\x10\x40\x01\xe0\x01",
                     b"911\x00\x40\x10\xe0\x01\x40\x01"):
            with self.subTest(info=info), self.assertRaises(ValueError):
                self.i2c.info = info
                self.module.Touch(i2c=self.i2c, reset=False)
        with self.assertRaises(ValueError):
            self.module.Touch(rotation=4, i2c=self.i2c)

    def test_packet_uses_little_endian_coordinates(self):
        self.assertEqual(self.module.decode_packet(packet(300, 470)), (1, 300, 470))

    def test_no_ready_bit_is_not_a_release_or_a_touch(self):
        self.assertIsNone(self.module.decode_packet(packet(300, 470, ready=False)))

    def test_zero_and_multiple_contacts_have_no_click_coordinates(self):
        for count in (0, 2, 3, 4, 5):
            self.assertEqual(self.module.decode_packet(packet(count=count)),
                             (count, None, None))

    def test_invalid_packets_rejected_without_clipping(self):
        for report in (b"", packet()[:-1], packet(320, 100), packet(100, 480),
                       packet(count=6), packet(track=128), packet(flags=0x40),
                       packet(flags=0x20), packet(flags=0x10)):
            with self.subTest(report=report), self.assertRaises(ValueError):
                self.module.decode_packet(report)

    def test_all_four_corners_for_every_display_rotation(self):
        corners = ((0, 0), (319, 0), (0, 479), (319, 479))
        expected = (
            corners,
            ((0, 319), (0, 0), (479, 319), (479, 0)),
            ((319, 479), (0, 479), (319, 0), (0, 0)),
            ((479, 0), (479, 319), (0, 0), (0, 319)),
        )
        for rotation in range(4):
            for raw, screen in zip(corners, expected[rotation]):
                with self.subTest(rotation=rotation, raw=raw):
                    self.assertEqual(self.module.rotate_point(*raw, rotation), screen)

    def test_transform_rejects_invalid_points_and_rotation(self):
        for args in ((-1, 0, 1), (0, -1, 1), (320, 0, 1), (0, 480, 1), (0, 0, 5)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.module.rotate_point(*args)

    def test_read_then_ack_at_single_fixed_i2c_address(self):
        self.i2c.next = packet(319, 479)
        self.assertEqual(self.touch.read_raw(), (1, 319, 479))
        self.assertEqual(self.i2c.calls, [
            ("info", 0x5D, 0x8140, 10, 16),
            ("read", 0x5D, 0x814E, 9, 16),
            ("ack", 0x5D, 0x814E, b"\x00", 16)])

    def test_unready_packet_is_not_acknowledged(self):
        self.touch.read_raw()
        self.assertEqual(len(self.i2c.calls), 2)

    def test_invalid_ready_packet_is_acknowledged_to_allow_recovery(self):
        self.i2c.next = packet(320, 0)
        with self.assertRaises(ValueError):
            self.touch.read_raw()
        self.assertEqual(self.i2c.calls[-1][0], "ack")

    def test_single_press_returns_landscape_coordinate(self):
        self.arm()
        self.assertEqual(self.poll(packet(10, 100)), (100, 309))

    def test_held_finger_and_drag_never_repeat(self):
        self.arm()
        self.assertEqual(self.poll(packet(10, 100)), (100, 309))
        for report in (packet(10, 100), packet(300, 470), packet(count=0, ready=False)):
            for _ in range(5):
                self.assertIsNone(self.poll(report, dt=100))

    def test_confirmed_debounced_release_allows_next_press(self):
        self.arm()
        self.poll(packet())
        self.release()
        self.assertEqual(self.poll(packet(20, 210)), (210, 299))

    def test_release_bounce_does_not_repeat_press(self):
        self.arm()
        self.poll(packet())
        self.poll(packet(count=0))
        self.assertIsNone(self.poll(packet(), dt=20))
        self.assertIsNone(self.poll(packet(), dt=100))
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_release_followed_by_fresh_press_after_interval(self):
        self.arm()
        self.poll(packet())
        self.poll(packet(count=0))
        self.assertIsNotNone(self.poll(packet(), dt=60))

    def test_boot_held_contact_requires_lift(self):
        self.assertIsNone(self.poll(packet()))
        self.assertIsNone(self.poll(packet(), dt=1000))
        self.assertIsNone(self.poll(dt=1000))
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_first_poll_with_old_held_finger_is_suppressed(self):
        self.assertIsNone(self.poll(packet(), dt=500))
        self.assertIsNone(self.poll(dt=500))

    def test_boot_release_frame_can_arm_without_an_extra_tap(self):
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_multitouch_requires_all_fingers_released(self):
        self.arm()
        self.assertIsNone(self.poll(packet(count=2)))
        self.assertIsNone(self.poll(packet()))
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_poll_rate_limits_i2c_transactions(self):
        self.arm()
        before = len(self.i2c.calls)
        for _ in range(9):
            self.assertIsNone(self.poll(dt=1))
        self.assertEqual(len(self.i2c.calls), before)
        self.poll(dt=1)
        self.assertEqual(len(self.i2c.calls), before + 1)

    def test_bus_error_backoff_and_release_before_recovery_press(self):
        self.arm()
        self.assertIsNone(self.poll(OSError("bus timeout")))
        self.assertEqual(self.touch.last_error, "bus timeout")
        self.assertEqual(self.touch.error_count, 1)
        before = len(self.i2c.calls)
        self.assertIsNone(self.poll(packet(), dt=249))
        self.assertEqual(len(self.i2c.calls), before)
        self.assertIsNone(self.poll(packet(), dt=1))
        self.assertIsNone(self.touch.last_error)
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_ack_failure_does_not_emit_or_repeat_press(self):
        self.arm()
        self.i2c.ack_error = OSError("ack failed")
        self.assertIsNone(self.poll(packet()))
        self.i2c.ack_error = None
        self.assertIsNone(self.poll(packet(), dt=250))
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_bad_packet_blocks_until_confirmed_release(self):
        self.arm()
        self.assertIsNone(self.poll(packet(400, 0)))
        self.assertIsNone(self.poll(packet(), dt=250))
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_clock_wrap_does_not_break_polling_or_debounce(self):
        self.now = self.PERIOD - 200
        self.touch = self.module.Touch(i2c=self.i2c, reset=False)
        self.arm()
        self.assertIsNotNone(self.poll(packet()))
        self.release()
        self.assertIsNotNone(self.poll(packet()))

    def test_diagnostics_is_cached_and_does_not_consume_report(self):
        self.i2c.next = packet()
        before = len(self.i2c.calls)
        self.touch.diagnostics()
        self.assertEqual(len(self.i2c.calls), before)


if __name__ == "__main__":
    unittest.main()
