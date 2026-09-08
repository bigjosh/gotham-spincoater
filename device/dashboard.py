"""Six-fan TFT dashboard; drawing only, with no hardware/control ownership."""

BG = 0x0842
CARD = 0x10C4
DIM_CARD = 0x1082
WHITE = 0xEF7D
MUTED = 0x7BEF
CYAN = 0x4E9E
GREEN = 0x45D2
RED = 0xF30C
TRACK = 0x2947


class Dashboard:
    def __init__(self, display):
        self.display = display
        self._cache = {}
        self._yield_hook = None
        display.fill(BG)
        display.hline(8, 68, 464, TRACK)
        display.fill_rect(8, 283, 226, 29, 0x1268)
        display.fill_rect(246, 283, 226, 29, 0x6004)
        display.text('LEFT: START', 33, 290, GREEN, scale=2, bg=0x1268)
        display.text('RIGHT: STOP', 271, 290, WHITE, scale=2, bg=0x6004)

    def set_yield_hook(self, callback):
        """Service bounded background work between complete drawing operations.

        The callback must not redraw this dashboard. Display methods have
        returned, so their SPI transaction is finished before it is called.
        """
        if callback is not None and not callable(callback):
            raise TypeError('yield hook must be callable or None')
        self._yield_hook = callback

    def _yield(self):
        if self._yield_hook is not None:
            self._yield_hook()

    def _text(self, key, value, x, y, width, scale=1, color=WHITE, bg=BG):
        value = str(value)[:width // (8 * scale)]
        signature = (value, color, bg)
        if self._cache.get(key) == signature:
            return
        self._cache[key] = signature
        self.display.fill_rect(x, y, width, 8 * scale, bg)
        self.display.text(value, x, y, color, scale=scale, bg=bg)
        self._yield()

    def update(self, snapshot):
        state = str(snapshot.get('state', 'IDLE')).upper()
        running = bool(snapshot.get('running', False))
        color = RED if state in ('ERROR', 'FAULT') else (CYAN if running else GREEN)
        self._text('state', state, 8, 9, 240, 2, color)
        target = snapshot.get('target_rpm', 0) or 0
        self._text('target', '%d RPM' % round(target), 280, 9, 192, 2, CYAN)
        self._text('recipe', snapshot.get('recipe_name', 'Default'), 8, 36, 304,
                   color=MUTED)
        self._text('elapsed', 'ELAPSED %5.1fs' % (snapshot.get('elapsed_s', 0) or 0),
                   328, 36, 144, color=MUTED)
        remaining = snapshot.get('phase_remaining_s')
        countdown = '--' if remaining is None else '%.1fs' % max(0, remaining)
        self._text('phase', 'STEP %s/%s   PHASE %s' % (
            snapshot.get('step', 0), snapshot.get('step_count', 0), countdown),
            8, 53, 464)
        fans = snapshot.get('fans', ())
        for channel in range(6):
            fan = fans[channel] if channel < len(fans) else {}
            self._fan(channel, fan, state == 'FAULT')
        self._text('message', snapshot.get('message', '') or 'Ready for the left START button',
                   8, 248, 464, color=RED if state in ('ERROR', 'FAULT') else MUTED)
        wifi = '%s  %s' % (snapshot.get('ssid', 'Wi-Fi'), snapshot.get('ip', ''))
        self._text('wifi', wifi, 8, 265, 368, color=CYAN)
        self._text('lag', 'LOOP %dms' % (snapshot.get('loop_lag_ms', 0) or 0),
                   384, 265, 88, color=MUTED)

    def _fan(self, channel, fan, run_fault=False):
        prefix = 'fan%d:' % channel
        enabled = bool(fan.get('enabled', False))
        x, y = 8 + (channel % 3) * 156, 76 + (channel // 3) * 84
        bg = CARD if enabled else DIM_CARD
        if self._cache.get(prefix + 'enabled') != enabled:
            for key in list(self._cache):
                if key.startswith(prefix):
                    del self._cache[key]
            self._cache[prefix + 'enabled'] = enabled
            self.display.fill_rect(x, y, 152, 80, bg)
            self.display.text('FAN #%d' % channel, x + 8, y + 7,
                              CYAN if enabled else MUTED, bg=bg)
            self._yield()
        duty = max(0, min(100, fan.get('duty', 0) or 0)) if enabled else 0
        fault = fan.get('fault') or (enabled and run_fault)
        self._text(prefix + 'status', 'FAULT' if fault else ('ON' if enabled else 'OFF'),
                   x + 96, y + 7, 48, color=RED if fault else MUTED, bg=bg)
        rpm = fan.get('rpm', 0) or 0
        if not enabled:
            number = '--'
        elif not fan.get('valid', False):
            number = '--'
        elif rpm >= 100000:
            number = '%dk' % round(rpm / 1000)
        else:
            number = '%d' % round(rpm)
        self._text(prefix + 'rpm', number, x + 8, y + 25, 136, 3,
                   RED if fault else (WHITE if enabled else MUTED), bg)
        self._text(prefix + 'power', 'PWM %3d%%' % round(duty),
                   x + 8, y + 56, 80, color=MUTED, bg=bg)
        self._text(prefix + 'unit', 'RPM', x + 112, y + 56, 32, color=MUTED, bg=bg)
        pixels = round(136 * duty / 100)
        if self._cache.get(prefix + 'bar') != (pixels, bool(fault)):
            self._cache[prefix + 'bar'] = (pixels, bool(fault))
            self.display.fill_rect(x + 8, y + 69, 136, 5, TRACK)
            if pixels:
                self.display.fill_rect(x + 8, y + 69, pixels, 5, RED if fault else CYAN)
            self._yield()
