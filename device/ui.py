"""Small, immediate-mode bench UI; no hardware imports for host preview."""
BG = 0x0863
PANEL = 0x10C5
EDGE = 0x298A
WHITE = 0xEF7D
MUTED = 0x8C93
CYAN = 0x361B
GREEN = 0x6F16
AMBER = 0xFDC9


class BenchUI:
    def __init__(self, display, pwm_hz=25000, pulses_per_rev=2, push_pull=False,
                 synchronous=False, channels=1):
        self.d = display
        self.cache = {}
        d = self.d
        d.fill(BG)
        d.fill_rect(0, 0, 480, 4, CYAN)
        d.text('GOTHAM / FAN 00', 18, 17, WHITE, 2, BG)
        d.text(('P12 PRO / SYNC TACH / %d CH' % channels) if synchronous else
               'P12 PRO / ' + ('PUSH-PULL' if push_pull else 'OPEN-DRAIN'),
               18, 42, MUTED, 1, BG)
        d.fill_rect(14, 65, 215, 105, PANEL)
        d.fill_rect(241, 65, 225, 105, PANEL)
        d.text('SET PWM', 26, 78, MUTED, 2, PANEL)
        d.text('MEASURED RPM', 253, 78, MUTED, 2, PANEL)
        d.hline(14, 224, 452, EDGE)
        d.text('PEAK RPM', 18, 235, MUTED, 1, BG)
        d.text('TACH Hz', 256, 235, MUTED, 1, BG)
        d.hline(14, 265, 452, EDGE)
        d.text('STICK L/R: 1%   U/D: 5%', 18, 275, MUTED, 1, BG)
        d.text('BTN1 START/PAUSE   BTN2 STOP', 18, 293, WHITE, 1, BG)
        d.text('%gkHz / %dPPR' % (pwm_hz / 1000, pulses_per_rev),
               362, 294, MUTED, 1, BG)

    def field(self, key, value, x, y, w, h, color, scale=1, bg=BG):
        token = (value, color)
        if self.cache.get(key) == token:
            return
        self.cache[key] = token
        self.d.fill_rect(x, y, w, h, bg)
        self.d.text(str(value), x, y, color, scale, bg)

    def update(self, requested, running, stats):
        rpm = stats['rpm']
        valid = stats['valid']
        status = 'RUNNING' if running else 'STOPPED'
        if running and requested == 0:
            status = 'ARMED'
        self.field('state', status, 350, 22, 116, 16,
                   GREEN if running else MUTED, 2)
        self.field('set', str(requested) + '%', 26, 110, 192, 48, CYAN, 5, PANEL)
        value = str(int(rpm + .5)) if valid else '--'
        scale = 5 if len(value) <= 5 else 4
        self.field('rpm', value, 253, 110, 208, 48, WHITE, scale, PANEL)
        output = requested if running else 0
        self.field('output', 'OUTPUT %3d%%' % output, 18, 185, 125, 16,
                   GREEN if output else MUTED)
        if self.cache.get('bar') != output:
            self.cache['bar'] = output
            self.d.fill_rect(147, 183, 315, 12, EDGE)
            if output:
                self.d.fill_rect(147, 183, int(315 * output / 100), 12, CYAN)
        if valid:
            message, color = 'TACH OK  /  LIVE MEASUREMENT', GREEN
        elif stats['pulses']:
            message, color = 'NO RECENT TACH PULSES', AMBER
        else:
            message, color = 'WAITING FOR TACH PULSES', MUTED
        others = [(i, reading) for i, reading in stats.get('channels', {}).items() if i != 0]
        if others:
            message = ' '.join('#%d:%s' % (i, str(round(r['rpm'])) if r['valid'] else '--')
                               for i, r in others)
            color = MUTED
        self.field('signal', message, 18, 207, 444, 8, color)
        self.field('peak', str(int(stats['peak_rpm'] + .5)), 104, 236, 140, 16, WHITE, 2)
        self.field('hz', '%.1f' % stats['hz'] if valid else '--', 332, 236, 130, 16, WHITE, 2)
