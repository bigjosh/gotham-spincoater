"""Log physical tach activity with the normal TFT, all PWM held at zero.

Run with guarded pico.ps1 Exec. START is ignored during the diagnostic.
No GPIO input overrides or positive power commands are used. Reboot afterward.
"""
from machine import mem32
from time import ticks_ms, ticks_diff
import main
import config

original_controller = main.Controller
original_poll = main.Portal.poll
original_update = main.Dashboard.update
controller = None
last_report = 0
levels = [None] * 6
edges = [0] * 6


class NoStart:
    def pressed(self, now):
        return False

    def block_until_release(self):
        pass


class DiagnosticController(original_controller):
    def __init__(self, *args, **kwargs):
        global controller
        super().__init__(*args, **kwargs)
        self.start_button = NoStart()
        controller = self


def poll_inputs(self):
    result = original_poll(self)
    for i, pair in enumerate(config.FUTURE_CHANNELS):
        # Raw pad reading, before GPIO input overrides.
        value = (mem32[0x40028000 + pair[1] * 8] >> 17) & 1
        if levels[i] is not None and value != levels[i]:
            edges[i] += 1
        levels[i] = value
    return result


def update_with_diagnostics(self, state):
    global last_report
    original_update(self, state)
    now = ticks_ms()
    if ticks_diff(now, last_report) < 1000:
        return
    last_report = now
    assert all(fan['duty'] == 0 for fan in state['fans'])
    assert all(fan.pwm_is_low() for fan in controller.fans.values())
    for i, pair in enumerate(config.FUTURE_CHANNELS):
        fan = state['fans'][i]
        driver = controller.fans[i]
        print('TACH_INPUT', i, 'level', levels[i], 'raw_edges', edges[i],
              'rpm', fan.get('rpm'), 'raw', fan.get('raw_rpm'),
              'valid', fan.get('valid'), 'period', fan.get('period_us'),
              'samples', fan.get('samples'), 'pulses', fan.get('pulses'),
              'error', fan.get('driver_error'), 'pc', mem32[driver._addr],
              'active', driver._sm.active(), 'starts', driver.capture_starts,
              'pad', hex(mem32[0x40038004 + pair[1] * 4]),
              'ctrl', hex(mem32[0x40028004 + pair[1] * 8]))


try:
    main.Controller = DiagnosticController
    main.Portal.poll = poll_inputs
    main.Dashboard.update = update_with_diagnostics
    print('TACH_DIAGNOSTIC_BEGIN 60 seconds; START ignored; all PWM zero')
    main.run(duration_ms=60000)
finally:
    main.Controller = original_controller
    main.Portal.poll = original_poll
    main.Dashboard.update = original_update
print('TACH_DIAGNOSTIC_END')
