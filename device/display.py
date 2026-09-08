"""Small direct-to-panel ST7796S driver for the 52Pi EP-0172 kit.

The kit uses SPI0 mode 0: GP2 SCK, GP3 MOSI, GP5 CS, GP6 DC, GP7 RST.
It has no required TFT readback connection; GP4 is deliberately not claimed.
Drawing is immediate. There is no full-screen framebuffer or show() call.
Colors are conventional RGB565 integers (red=0xF800, blue=0x001F).

Defaults follow the manufacturer's landscape MADCTL=0x28 and INVON:
https://github.com/geeekpi/pico_breadboard_kit/blob/master/lv_port_disp.c
The minimal ST77xx initialization / small-framebuffer approach is adapted
from Salvatore Sanfilippo's MIT-licensed ST77xx-pure-MP. See
THIRD_PARTY_NOTICES.md. The kit's MicroPython example also uses that driver:
https://github.com/GauthierDefrance/Pico-Breadboard-Kit-Plus-version-MicroPython
"""

from machine import Pin, SPI
from time import sleep_ms
import framebuf


def rgb565(red, green, blue):
    """Convert three 0..255 channels to a conventional RGB565 integer."""
    return ((red & 248) << 8) | ((green & 252) << 3) | (blue >> 3)


class Display:
    """480x320 landscape panel; about 1.5 KB of persistent drawing buffers.

    Pass inversion=False or bgr=False for a different panel lot. rotation
    is clockwise quarter turns of the native 320x480 display: 1 is the
    kit's landscape orientation and 3 turns that landscape upside down.
    An injected SPI object is useful for host tests. GPIO allocation and
    reset happen only when an instance is constructed.
    """

    def __init__(self, width=480, height=320, baudrate=40000000,
                 rotation=1, inversion=True, bgr=True, spi=None):
        expected = (480, 320) if rotation % 2 else (320, 480)
        if (width, height) != expected:
            raise ValueError("width/height do not match panel rotation")
        self.width = width
        self.height = height
        self.cs = Pin(5, Pin.OUT, value=1)
        self.dc = Pin(6, Pin.OUT, value=1)
        self.reset = Pin(7, Pin.OUT, value=1)
        self.spi = spi if spi is not None else SPI(
            0, baudrate=baudrate, polarity=0, phase=0, bits=8,
            sck=Pin(2), mosi=Pin(3), miso=None)
        self._command_byte = bytearray(1)
        self._address = bytearray(4)
        self._row = bytearray(480 * 2)
        self._row_view = memoryview(self._row)
        self._mono_data = bytearray(480)
        self._mono = framebuf.FrameBuffer(
            self._mono_data, 480, 8, framebuf.MONO_HLSB)
        self._fill_color = None
        self._fill_pixels = 0

        self.reset(1)
        sleep_ms(50)
        self.reset(0)
        sleep_ms(50)
        self.reset(1)
        sleep_ms(150)
        self._command(0x01)  # Software reset.
        sleep_ms(150)
        self._command(0x11)  # Sleep out.
        sleep_ms(120)
        self._command(0x3A, b"\x55")  # RGB565, 16 bits per pixel.
        self.configure(rotation=rotation, inversion=inversion, bgr=bgr)
        self._command(0x13)  # Normal display mode.
        self.fill(0x0000)
        self._command(0x29)  # Display on.
        sleep_ms(100)

    def _command(self, command, data=None):
        self.cs(0)
        try:
            self.dc(0)
            self._command_byte[0] = command
            self.spi.write(self._command_byte)
            if data is not None:
                self.dc(1)
                self.spi.write(data)
        finally:
            self.cs(1)

    def configure(self, rotation=None, inversion=None, bgr=None):
        """Change panel orientation/color options; redraw afterwards.

        rotation=1/3 gives 480x320, rotation=0/2 gives 320x480. Setting
        inversion changes the panel's electrical inversion mode, without
        changing the public RGB565 color representation.
        """
        if rotation is not None:
            if rotation not in (0, 1, 2, 3):
                raise ValueError("rotation must be 0, 1, 2, or 3")
            self.rotation = rotation
        if bgr is not None:
            self.bgr = bool(bgr)
        if inversion is not None:
            self.inversion = bool(inversion)
        self.width, self.height = ((480, 320) if self.rotation % 2
                                   else (320, 480))
        madctl = (0x40, 0x20, 0x80, 0xE0)[self.rotation]
        if self.bgr:
            madctl |= 0x08
        self._command(0x36, bytes((madctl,)))
        self._command(0x21 if self.inversion else 0x20)

    def _range(self, command, start, end):
        data = self._address
        data[0] = start >> 8
        data[1] = start & 255
        data[2] = end >> 8
        data[3] = end & 255
        self._command(command, data)

    def _window(self, x, y, width, height):
        self._range(0x2A, x, x + width - 1)
        self._range(0x2B, y, y + height - 1)
        self._command(0x2C)

    def fill(self, color):
        self.fill_rect(0, 0, self.width, self.height, color)

    def fill_rect(self, x, y, width, height, color):
        """Fill a rectangle, clipped on every edge; nonpositive sizes do nothing."""
        right = min(self.width, x + width)
        bottom = min(self.height, y + height)
        x, y = max(0, x), max(0, y)
        width, height = right - x, bottom - y
        if width <= 0 or height <= 0:
            return
        color &= 0xFFFF
        if color != self._fill_color or width > self._fill_pixels:
            high, low = color >> 8, color & 255
            for offset in range(0, width * 2, 2):
                self._row[offset] = high
                self._row[offset + 1] = low
            self._fill_color = color
            self._fill_pixels = width
        self._window(x, y, width, height)
        row = self._row_view[:width * 2]
        self.cs(0)
        self.dc(1)
        try:
            for _ in range(height):
                self.spi.write(row)
        finally:
            self.cs(1)

    def hline(self, x, y, width, color):
        self.fill_rect(x, y, width, 1, color)

    def vline(self, x, y, height, color):
        self.fill_rect(x, y, 1, height, color)

    def rect(self, x, y, width, height, color):
        if width <= 0 or height <= 0:
            return
        self.hline(x, y, width, color)
        self.hline(x, y + height - 1, width, color)
        self.vline(x, y, height, color)
        self.vline(x + width - 1, y, height, color)

    def text(self, text, x, y, color, scale=1, bg=None):
        """Draw MicroPython's 8x8 font, scaled by an integer from 1 to 16.

        Supplying bg is much faster: each text line uses a single display
        window and row transfers. bg=None preserves existing background
        using horizontal foreground runs. Newlines advance 8*scale pixels.
        Text clips on every edge and does not automatically wrap.
        """
        if not isinstance(scale, int) or not 1 <= scale <= 16:
            raise ValueError("scale must be an integer from 1 to 16")
        for line in str(text).split("\n"):
            self._text_line(line, x, y, color, scale, bg)
            y += 8 * scale

    def _text_line(self, text, x, y, color, scale, bg):
        left, top = max(0, x), max(0, y)
        right = min(self.width, x + len(text) * 8 * scale)
        bottom = min(self.height, y + 8 * scale)
        if left >= right or top >= bottom:
            return
        # Offset the source strip as well, so text far beyond the left
        # edge can be clipped without allocating a framebuffer for it.
        source_start = (left - x) // scale
        self._mono.fill(0)
        self._mono.text(text, -source_start, 0, 1)
        if bg is None:
            self._transparent_text(left, top, right, bottom,
                                   x, y, source_start, color, scale)
            return

        self._fill_color = None  # Row buffer will contain varying colors.
        foreground = ((color >> 8) & 255, color & 255)
        background = ((bg >> 8) & 255, bg & 255)
        row = self._row_view[:(right - left) * 2]
        self._window(left, top, right - left, bottom - top)
        self.cs(0)
        self.dc(1)
        try:
            previous_source_y = -1
            for screen_y in range(top, bottom):
                source_y = (screen_y - y) // scale
                if source_y != previous_source_y:
                    offset = 0
                    for screen_x in range(left, right):
                        source_x = (screen_x - x) // scale - source_start
                        pair = (foreground if self._mono.pixel(source_x, source_y)
                                else background)
                        self._row[offset] = pair[0]
                        self._row[offset + 1] = pair[1]
                        offset += 2
                    previous_source_y = source_y
                self.spi.write(row)
        finally:
            self.cs(1)

    def _transparent_text(self, left, top, right, bottom,
                          x, y, source_start, color, scale):
        # Combine adjoining foreground pixels into one transfer. This
        # preserves background without a framebuffer/readback of the TFT.
        screen_y = top
        while screen_y < bottom:
            source_y = (screen_y - y) // scale
            rows = min(bottom - screen_y, scale - (screen_y - y) % scale)
            run_start = None
            for screen_x in range(left, right + 1):
                source_x = (screen_x - x) // scale - source_start
                on = (screen_x < right and
                      self._mono.pixel(source_x, source_y))
                if on and run_start is None:
                    run_start = screen_x
                elif not on and run_start is not None:
                    self.fill_rect(run_start, screen_y,
                                   screen_x - run_start, rows, color)
                    run_start = None
            screen_y += rows
