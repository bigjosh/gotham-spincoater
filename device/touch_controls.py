"""Core-0 touch routing; no direct access to PWM/PIO or control-core state."""

try:
    from time import ticks_diff
except ImportError:
    def ticks_diff(new, old):
        return (new - old + (1 << 29)) % (1 << 30) - (1 << 29)


class FanTouchControls:
    POLL_MS = 20
    NOTICE_MS = 4000

    def __init__(self, touch, dashboard, controller):
        self.touch = touch
        self.dashboard = dashboard
        self.controller = controller
        self._last_poll = None
        self._notice_since = None
        self.error = None

    def _notice(self, text, now_ms):
        self.dashboard.set_notice(text)
        self._notice_since = now_ms

    def _read_error(self, detail, now_ms):
        if self.error != detail:
            print('TOUCH_ERROR ' + detail)
            self._notice('Touch read failed; lift and try again', now_ms)
        self.error = detail

    def poll(self, now_ms):
        if (self._notice_since is not None and
                ticks_diff(now_ms, self._notice_since) >= self.NOTICE_MS):
            self.dashboard.set_notice(None)
            self._notice_since = None
        if (self._last_poll is not None and
                ticks_diff(now_ms, self._last_poll) < self.POLL_MS):
            return
        self._last_poll = now_ms
        try:
            point = self.touch.poll()
        except OSError as error:
            self._read_error(str(error), now_ms)
            return
        detail = getattr(self.touch, 'last_error', None)
        if detail is not None:
            self._read_error(str(detail), now_ms)
            return
        self.error = None
        if point is None:
            return
        channel = self.dashboard.hit_test(*point)
        if channel is None:
            return
        snapshot = self.controller.snapshot()
        fan = snapshot['fans'][channel]
        if not fan.get('available', True):
            self._notice('Fan #%d locked: GPIO modification required' % channel, now_ms)
            return
        if snapshot.get('running'):
            self._notice('Stop the recipe before changing fans', now_ms)
            return
        enabled = not fan['enabled']
        try:
            # Controller rechecks START/edit state under its own lock, saves
            # on core 0, and delegates all live driver changes to core 1.
            self.controller.set_fan_enabled(channel, enabled)
        except (OSError, ValueError, RuntimeError) as error:
            self._notice(str(error), now_ms)
            print('FAN_SETTING_ERROR #%d %s' % (channel, error))
            return
        label = 'enabled' if enabled else 'disabled'
        self._notice('Fan #%d %s; selection saved' % (channel, label), now_ms)
        print('FAN_SETTING #%d %s' % (channel, label))
