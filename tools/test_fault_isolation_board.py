"""Run the real control engine on MicroPython with scripted tach readings.

Uses no GPIO, PIO, motor commands or settings files. This verifies firmware
compatibility and recipe behavior, not physical fan speed. Run with the normal
app stopped via pico.ps1 Exec, then reboot to restore the normal application.
"""
from control import ControlEngine
from recipes import DEFAULT_SETTINGS


PROFILE = {'name': 'Fault isolation check', 'steps': [
    {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
    {'rpm': 3000, 'slew_s': 0, 'dwell_s': 3},
    {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
]}
SETTINGS = dict(DEFAULT_SETTINGS)
SETTINGS.update(settle_s=0.1, reach_timeout_s=5)
CHANNELS = tuple(range(6))


def readings(rpm):
    return {i: {'rpm': rpm, 'valid': True} for i in CHANNELS}


engine = ControlEngine(CHANNELS)
engine.start(PROFILE, SETTINGS, 0)
now = 0
while engine.snapshot()['step'] == 1 and now < 1000:
    now += 20
    engine.update(now, readings(0))
assert engine.snapshot()['step'] == 2, engine.snapshot()

# A stopped PIO with its own fault must not become a global STOP event.
sample = readings(1500)
sample[0].update(fault='command_underrun', stopped=True)
now += 20
engine.update(now, sample)
assert engine.running and engine.faults[0] == 'command_underrun'
assert engine.duties[0] == 0 and all(engine.duties[i] > 0 for i in range(1, 6))
assert engine.participating == (1, 2, 3, 4, 5)
print('LOCAL_PIO_FAULT_OK other five seekers continue')

for _ in range(20):
    now += 20
    sample = readings(3000)
    sample[0].update(stopped=True)
    engine.update(now, sample)
assert engine.state == 'DWELL', engine.snapshot()
held_before = engine.snapshot()['phase_remaining_s']

# Lose another fan's established tach during dwell. Its peers keep their
# power and the already accumulated dwell survives its eventual exclusion.
for _ in range(80):
    now += 20
    sample = readings(3000)
    sample[1] = {'valid': False}
    engine.update(now, sample)
assert engine.running and engine.faults[1], engine.snapshot()
assert engine.participating == (2, 3, 4, 5)
assert engine.duties[0] == engine.duties[1] == 0
assert all(engine.duties[i] > 0 for i in engine.participating)
assert engine.snapshot()['phase_remaining_s'] <= held_before
print('LOCAL_TACH_FAULT_OK remaining four resume accumulated dwell')

for _ in range(250):
    now += 20
    rpm = 0 if engine.snapshot()['step'] == 3 else 3000
    engine.update(now, readings(rpm))
    if not engine.running:
        break
assert engine.state == 'COMPLETE', engine.snapshot()
assert engine.snapshot()['fault_count'] == 2
assert engine.enabled == CHANNELS and engine.duties == [0] * 6
print('COMPLETE_WITH_FAULTS_OK selection preserved, all duties zero')

engine.start(PROFILE, SETTINGS, now)
assert engine.participating == CHANNELS and not any(engine.faults)
now += 20
sample = readings(0)
sample[4]['stopped'] = True
engine.update(now, sample)
assert engine.state == 'STOPPED' and engine.duties == [0] * 6
print('RETRY_AND_GLOBAL_STOP_OK fresh START clears faults, STOP stops all')
print('FAULT_ISOLATION_BOARD_OK scripted tach only; no motor commands')
