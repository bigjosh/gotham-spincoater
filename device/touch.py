"""GT911 touch input for the 52Pi EP-0172, polled on the UI core only.

Protocol/pin facts: manufacturer's pico2/lv_port_indev.[ch] and portrait
display initialization in pico2/lv_port_disp.c:
https://github.com/geeekpi/pico_breadboard_kit/tree/pico2
Reset/address timing: Goodix's gtp_reset_guitar()/gtp_int_sync():
https://github.com/goodix/gt9xx_driver_android/blob/master/gt9xx.c
This is an independent implementation; no vendor driver code is copied.

No controller configuration is rewritten.  GP8/9 are I2C0 SDA/SCL; GP10
is reset and GP11 is INT/address selection.  The only I2C address used is
0x5D.  INT is floating input after initialization, not a Python IRQ.
"""

from machine import I2C, Pin
from time import sleep_ms, ticks_diff, ticks_ms


ADDRESS = 0x5D
INFO_REGISTER = 0x8140
STATUS_REGISTER = 0x814E
POLL_MS = 10
RELEASE_MS = 50
BOOT_QUIET_MS = 200
ERROR_RETRY_MS = 250


def decode_packet(packet, width=320, height=480):
    """Decode status plus the first eight-byte GT911 point record.

    Return None for no fresh report, or (contact_count, raw_x, raw_y).
    Zero/multiple contacts have None coordinates.  Reject malformed or
    out-of-range data instead of clipping it into a clickable UI button.
    """
    if len(packet) != 9:
        raise ValueError("short GT911 packet")
    status = packet[0]
    if not status & 0x80:
        return None
    count = status & 0x0F
    if status & 0x70 or count > 5:
        raise ValueError("unexpected GT911 status")
    if count != 1:
        return count, None, None
    if packet[1] & 0xF0:
        raise ValueError("unexpected GT911 track ID")
    x = packet[2] | (packet[3] << 8)
    y = packet[4] | (packet[5] << 8)
    if x >= width or y >= height:
        raise ValueError("GT911 coordinate out of range")
    return 1, x, y


def rotate_point(x, y, rotation=1):
    """Match Display's MADCTL values, from the kit's raw 320x480 axes.

    Vendor examples use raw touch with portrait MADCTL=0x48.  Display's
    rotation 1 changes that to 0x28: transpose axes and invert raw X.
    Endpoint subtraction is dimension minus ONE, keeping corners in range.
    """
    if not (0 <= x < 320 and 0 <= y < 480):
        raise ValueError("raw touch point out of range")
    if rotation == 0:
        return x, y
    if rotation == 1:
        return y, 319 - x
    if rotation == 2:
        return 319 - x, 479 - y
    if rotation == 3:
        return 479 - y, x
    raise ValueError("rotation must be 0, 1, 2, or 3")


class Touch:
    """Return one (x, y) from poll() for each distinct single-finger press.

    A held finger or drag never repeats.  A multitouch report, malformed
    packet or I2C error suppresses input until a confirmed release.  An
    initial held finger is also ignored; an initially untouched controller
    becomes ready after 200 ms of polling.  Normal release debounce is 50 ms.

    Constructor errors propagate so main can show touch unavailable while
    keeping fan control and physical STOP running.  poll() catches bus/data
    errors, stores last_error/error_count, and retries after 250 ms.  Each
    poll does one nine-byte read and at most one acknowledgement, without
    retry loops.  The RP2 hardware I2C timeout is 2 ms per bus transfer.

    read_raw() reads/acknowledges one report and raises on errors; it bypasses
    tap filtering, so use it instead of poll() only for live diagnostics.
    diagnostics() returns cached identity/state without consuming a report.
    reset=False attaches to an already initialized controller for probing.
    """

    def __init__(self, rotation=1, i2c=None, reset=True):
        if rotation not in (0, 1, 2, 3):
            raise ValueError("rotation must be 0, 1, 2, or 3")
        self.rotation = rotation
        self.width, self.height = ((480, 320) if rotation % 2
                                   else (320, 480))
        self.irq = Pin(11, Pin.IN)  # No internal pull on Goodix INT.
        self.reset_pin = Pin(10)
        if reset:
            self.reset_pin.init(Pin.OUT, value=0)
            sleep_ms(20)
            self.irq.init(Pin.OUT, value=0)  # LOW selects 7-bit address 0x5D.
            sleep_ms(2)
            self.reset_pin.init(Pin.OUT, value=1)
            sleep_ms(6)
            # Retain reset HIGH; INT stays LOW for the synchronization period.
            sleep_ms(50)
            self.irq.init(Pin.IN, pull=None)
        # RP2 v1.29.0 explicitly supports timeout on hardware I2C:
        # https://github.com/micropython/micropython/blob/v1.29.0/ports/rp2/machine_i2c.c
        self.i2c = i2c if i2c is not None else I2C(
            0, sda=Pin(8), scl=Pin(9), freq=100000, timeout=2000)
        info = self.i2c.readfrom_mem(ADDRESS, INFO_REGISTER, 10, addrsize=16)
        if len(info) != 10 or info[:3] != b"911":
            raise ValueError("GT911 controller not identified")
        self.product_id = info[:4].decode().rstrip("\x00")
        self.firmware_version = info[4] | (info[5] << 8)
        self.raw_width = info[6] | (info[7] << 8)
        self.raw_height = info[8] | (info[9] << 8)
        if (self.raw_width, self.raw_height) != (320, 480):
            raise ValueError("unexpected GT911 raw resolution: %dx%d" %
                             (self.raw_width, self.raw_height))
        self._packet = bytearray(9)
        self._ack = b"\x00"
        self._last_poll = None
        self._last_failure = None
        self._release_since = None
        self._boot_since = ticks_ms()
        self._boot = True
        self._armed = False
        self.last_error = None
        self.error_count = 0
        self.last_raw = None

    def read_raw(self):
        """Read/ack one report: None, or (count, raw_x, raw_y)."""
        self.i2c.readfrom_mem_into(
            ADDRESS, STATUS_REGISTER, self._packet, addrsize=16)
        ready = bool(self._packet[0] & 0x80)
        try:
            raw = decode_packet(self._packet, self.raw_width, self.raw_height)
        finally:
            # Read coordinates BEFORE clearing ready.  A malformed ready
            # report must also be cleared so the controller can replace it.
            if ready:
                self.i2c.writeto_mem(
                    ADDRESS, STATUS_REGISTER, self._ack, addrsize=16)
        self.last_raw = raw
        return raw

    def _suppress_until_release(self):
        self._armed = False
        self._boot = False
        self._release_since = None

    def poll(self):
        """Bounded UI-core polling; None means no new button press."""
        now = ticks_ms()
        if self._last_poll is not None and ticks_diff(now, self._last_poll) < POLL_MS:
            return None
        if (self._last_failure is not None and
                ticks_diff(now, self._last_failure) < ERROR_RETRY_MS):
            return None
        self._last_poll = now
        try:
            raw = self.read_raw()
        except (OSError, ValueError) as error:
            self.last_error = str(error)
            self.error_count += 1
            self._last_failure = now
            self._suppress_until_release()
            return None
        self.last_error = None
        self._last_failure = None

        # Only a previously observed release can re-arm an ordinary press.
        # No fresh report alone cannot release a held/stationary finger.
        if (self._release_since is not None and
                ticks_diff(now, self._release_since) >= RELEASE_MS):
            self._armed = True
            self._release_since = None
        if raw is None:
            if self._boot and ticks_diff(now, self._boot_since) >= BOOT_QUIET_MS:
                self._armed = True
                self._boot = False
            return None
        count, x, y = raw
        if count == 0:
            if not self._armed and self._release_since is None:
                self._release_since = now
            self._boot = False
            return None
        was_armed = self._armed
        self._suppress_until_release()
        if count == 1 and was_armed:
            return rotate_point(x, y, self.rotation)
        return None

    def diagnostics(self):
        """Cached protocol diagnostics; does not read/ack touch reports."""
        return {"address": ADDRESS, "product_id": self.product_id,
                "firmware_version": self.firmware_version,
                "raw_resolution": (self.raw_width, self.raw_height),
                "rotation": self.rotation, "armed": self._armed,
                "last_raw": self.last_raw, "last_error": self.last_error,
                "error_count": self.error_count}
