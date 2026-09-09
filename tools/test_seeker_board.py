"""Live, authorized single-fan RPM/recipe test. STOP remains active in PIO.

Run only on the identified project board with an unloaded fan on GP18/19.
The test always stops and releases its driver before returning to the REPL.
"""
import sys
sys.modules.pop('control', None)
from control import ControlEngine
from recipes import DEFAULT_SETTINGS
from pio_fan import PioFan
from time import ticks_ms, ticks_diff, sleep_ms

print('SEEK_GAIN', ControlEngine.SEEK_GAIN)
assert ControlEngine.SEEK_GAIN == 0.01, 'Deploy the current control.py first'

profile = {'name': 'Bench speed jumps', 'steps': [
    {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
    {'rpm': 1200, 'slew_s': 4, 'dwell_s': 2},
    {'rpm': 2400, 'slew_s': 6, 'dwell_s': 3},
    {'rpm': 1600, 'slew_s': 0, 'dwell_s': 2},
    {'rpm': 3000, 'slew_s': 5, 'dwell_s': 5},
    {'rpm': 0, 'slew_s': 4, 'dwell_s': 0},
]}
fan = PioFan(18, 19, 0, stop_pin=14)
engine = ControlEngine((0,))
try:
    began = ticks_ms()
    report = -1000
    previous = 0.0
    previous_time = began
    engine.start(profile, DEFAULT_SETTINGS, began)
    while engine.running and ticks_diff(ticks_ms(), began) < 110000:
        now = ticks_ms()
        reading = fan.sample()
        if reading.get('fault'):
            raise RuntimeError('Fan driver error: ' + str(reading['fault']))
        duty = engine.update(now, {0: reading})[0]
        applied = fan.set_duty(duty)
        dt = max(0, ticks_diff(now, previous_time)) / 1000
        if duty > previous:
            assert duty - previous <= DEFAULT_SETTINGS['max_power_per_s'] * dt + 0.001
        previous, previous_time = duty, now
        elapsed = ticks_diff(now, began)
        if elapsed - report >= 1000:
            print('BENCH t=%.1f state=%s step=%d target=%.0f rpm=%s duty=%.1f overflow=%d' %
                  (elapsed / 1000, engine.state, engine._index + 1,
                   engine.target_rpm, str(round(reading['rpm'])) if reading['valid'] else '--',
                   applied, reading['overflows']))
            report = elapsed
        sleep_ms(20)
    print('BENCH_RESULT', engine.snapshot())
    assert engine.state == 'COMPLETE', engine.snapshot()
    print('SEEKER_BOARD_PASS')
finally:
    fan.close()
