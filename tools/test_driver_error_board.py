"""Check six live PIO drivers and core-1 driver-error handling at zero power.

Injects one driver error on its owning core. The test recipe contains ONLY
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
    source = 'zero-power driver-error test'
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


def sample_with_driver_error(self):
    global injected
    if self._sm_id == config.TACH_STATE_MACHINES[0] and not injected:
        injected = True
        self._fault = 'command_underrun'
        self.stop()  # This wrapper executes only on the driver-owning core.
    return original_sample(self)


controller = None
try:
    controller = Controller(RecipeBook(), fan_settings=TestSelection())
    controller._profile = validate_profile({'name': 'Zero-power driver-error check', 'steps': [
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 30},
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
    ]})
    controller.start_button = OneStart()
    PioFan.sample = sample_with_driver_error
    controller.start_thread()
    began = ticks_ms()
    observed = 0
    while ticks_diff(ticks_ms(), began) < 4000:
        sleep_ms(100)
        state = controller.snapshot()
        if sum(bool(fan.get('driver_error')) for fan in state['fans']) != 1:
            assert not controller.finished, controller.error
            continue
        assert state['running'] and state['target_rpm'] == 0, state
        assert state['fans'][0]['driver_error'] == 'command_underrun', state
        assert all(fan['participating'] for fan in state['fans']), state
        assert all(fan['enabled'] and fan['duty'] == 0 for fan in state['fans'])
        assert controller.fans[0]._stopped
        assert all(not controller.fans[i]._stopped for i in range(1, 6))
        assert all(Pin(config.FUTURE_CHANNELS[i][0]).value() == 0 for i in range(6))
        assert not controller.closing and not controller.finished, controller.error
        assert ticks_diff(ticks_ms(), controller.heartbeat) < 500
        observed += 1
        if observed == 10:
            break
    assert observed == 10, ('No stable driver-error observation', controller.snapshot())
    print('CORE1_DRIVER_ERROR_OK fan0 parked; other five PIO drivers stay armed')
    print('SIX_PWM_LOW_OK target=0 duty=0; heartbeat current; saved files untouched')
finally:
    try:
        if controller is not None:
            controller.close()
    finally:
        PioFan.sample = original_sample
print('DRIVER_ERROR_BOARD_OK all outputs closed LOW; no positive-power commands')
