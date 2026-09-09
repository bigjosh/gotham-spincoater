"""Check six live PIO drivers and core-1 fault isolation at zero power only.

Injects one driver fault on its owning core. The test recipe contains ONLY
zero RPM steps; every PWM remains LOW. Saved selections/recipes are not read
or written. Run via pico.ps1 Exec with the normal app stopped, then reboot.
"""
from time import sleep_ms, ticks_ms, ticks_diff
from machine import Pin
from recipes import RecipeBook, validate_profile
from runtime import Controller
from pio_fan import PioFan
import config


class TestSelection:
    source = 'zero-power fault test'
    load_error = None

    def load(self):
        return tuple(range(6))


class OneStart:
    pending = True

    def pressed(self, now):
        result = self.pending
        self.pending = False
        return result

    def block_until_release(self):
        self.pending = False


if not config.AUX_LINKS_DISCONNECTED:
    raise RuntimeError('All-six test requires the confirmed GPIO modifications')

original_sample = PioFan.sample
injected = False


def sample_with_local_fault(self):
    global injected
    if self._sm_id == config.TACH_STATE_MACHINES[0] and not injected:
        injected = True
        self._fault = 'command_underrun'
        self.stop()  # This wrapper executes only on the driver-owning core.
    return original_sample(self)


controller = None
try:
    controller = Controller(RecipeBook(), fan_settings=TestSelection())
    controller._profile = validate_profile({'name': 'Zero-power fault check', 'steps': [
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 30},
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
    ]})
    controller.start_button = OneStart()
    PioFan.sample = sample_with_local_fault
    controller.start_thread()
    began = ticks_ms()
    observed = 0
    while ticks_diff(ticks_ms(), began) < 4000:
        sleep_ms(100)
        state = controller.snapshot()
        if state['fault_count'] != 1:
            assert not controller.finished, controller.error
            continue
        assert state['running'] and state['target_rpm'] == 0, state
        assert state['fans'][0]['fault'] == 'command_underrun', state
        assert not state['fans'][0]['participating']
        assert all(state['fans'][i]['participating'] for i in range(1, 6)), state
        assert all(fan['enabled'] and fan['duty'] == 0 for fan in state['fans'])
        assert controller.fans[0]._stopped
        assert all(not controller.fans[i]._stopped for i in range(1, 6))
        assert all(Pin(config.FUTURE_CHANNELS[i][0]).value() == 0 for i in range(6))
        assert not controller.closing and not controller.finished, controller.error
        assert ticks_diff(ticks_ms(), controller.heartbeat) < 500
        observed += 1
        if observed == 10:
            break
    assert observed == 10, ('No stable isolated-fault observation', controller.snapshot())
    print('CORE1_LOCAL_FAULT_OK fan0 parked; other five PIO drivers stay armed')
    print('SIX_PWM_LOW_OK target=0 duty=0; heartbeat current; saved files untouched')
finally:
    try:
        if controller is not None:
            controller.close()
    finally:
        PioFan.sample = original_sample
print('FAULT_RUNTIME_BOARD_OK all outputs closed LOW; no positive-power commands')
