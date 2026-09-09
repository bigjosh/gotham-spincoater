"""Run RPM warning/recipe checks on MicroPython with scripted readings.

No GPIO, PIO or motor commands. Run via the guarded pico.ps1 Exec helper,
with the normal app stopped; reboot into the normal application afterward.
"""
from control import ControlEngine
from recipes import DEFAULT_SETTINGS


PROFILE = {'name': 'RPM warning check', 'steps': [
    {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
    {'rpm': 3000, 'slew_s': 0, 'dwell_s': 20},
    {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
]}
SETTINGS = dict(DEFAULT_SETTINGS)
SETTINGS.update(rpm_warning_percent=5, rpm_warning_delay_s=0.2)
CHANNELS = tuple(range(6))


def readings(rpm=3000):
    return {i: {'rpm': rpm, 'valid': True} for i in CHANNELS}


engine = ControlEngine(CHANNELS)
engine.start(PROFILE, SETTINGS, 0)
for now in range(0, 501, 20):
    sample = readings()
    sample[0]['rpm'] = 2500
    engine.update(now, sample)
state = engine.snapshot()
assert engine.running and state['fans'][0]['warning'], state
assert engine.duties[0] > 0 and engine.participating == CHANNELS
assert not any(fan['warning'] for fan in state['fans'][1:])
drive = engine.duties[0]
print('TIMED_WARNING_OK fan0 red, still driven; five peers in bounds')

sample[0]['rpm'] = 2950
now += 20
engine.update(now, sample)
assert not engine.snapshot()['fans'][0]['warning']
assert engine.snapshot()['fans'][0]['out_of_bounds_s'] == 0
assert engine.duties[0] == drive and engine.running
print('IMMEDIATE_RECOVERY_OK first in-bounds reading clears red without START')

# Missing established tach continues drive instead of shutting down, even
# after the old 1.5-second and eight-second tach failure windows.
sample[0] = {'valid': False}
for _ in range(450):
    now += 20
    engine.update(now, sample)
assert engine.running and engine.duties[0] == drive
assert engine.snapshot()['fans'][0]['warning']
assert engine.participating == CHANNELS
print('MISSING_TACH_CONTINUES_OK last power held for nine seconds')

while now < 20020 and engine.running:
    now += 20
    engine.update(now, sample)
assert engine.state == 'COMPLETE' and now == 20000, (now, engine.snapshot())
assert engine.duties == [0] * 6 and engine.snapshot()['warning_count'] == 0
print('SCHEDULE_OK completed at20s despite missing RPM; all duties zero')

engine.start(PROFILE, SETTINGS, now)
sample = readings()
sample[2]['stopped'] = True
engine.update(now, sample)
assert engine.state == 'STOPPED' and engine.duties == [0] * 6
print('GLOBAL_STOP_OK every requested output zero')
print('RPM_WARNING_BOARD_OK scripted readings only; no motor commands')
