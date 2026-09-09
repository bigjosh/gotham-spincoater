"""Exercise warning and recovery with six live PIOs and zero PWM only.

The temporary recipe contains ONLY zero targets. Tach values are synthetic:
fan0 appears to coast at500 RPM then reports zero. Its driver must stay armed
through the warning and recovery. No saved selections/recipes are accessed.
Run via guarded pico.ps1 Exec, then reboot into the normal app.
"""
from time import sleep_ms, ticks_ms, ticks_diff
from machine import Pin
from recipes import RecipeBook, validate_profile
from runtime import Controller
from pio_fan import PioFan
import config


class TestSelection:
    source = 'zero-power RPM warning test'
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
    raise RuntimeError('All-six test requires confirmed GPIO modifications')

original_sample = PioFan.sample
sample_began = None


def sample_with_scripted_rpm(self):
    global sample_began
    result = original_sample(self)
    now = ticks_ms()
    if sample_began is None:
        sample_began = now
    # Only replace tach data; retain all real hardware STOP/error reporting.
    high = self._sm_id == config.TACH_STATE_MACHINES[0] and ticks_diff(now, sample_began) < 1000
    result.update(rpm=500 if high else 0, valid=True, age_ms=0)
    return result


controller = None
seen_warning = recovered = False
try:
    controller = Controller(RecipeBook(), fan_settings=TestSelection())
    controller._profile = validate_profile({'name': 'Zero-power warning check', 'steps': [
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 10},
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
    ]})
    controller._settings.update(rpm_warning_percent=5, rpm_warning_delay_s=0.2)
    controller.start_button = OneStart()
    PioFan.sample = sample_with_scripted_rpm
    controller.start_thread()
    began = ticks_ms()
    while ticks_diff(ticks_ms(), began) < 3000:
        sleep_ms(50)
        state = controller.snapshot()
        assert not controller.finished and not controller.closing, controller.error
        assert state['running'] and state['target_rpm'] == 0, state
        assert all(fan['participating'] and fan['duty'] == 0 for fan in state['fans'])
        assert all(not controller.fans[i]._stopped for i in range(6))
        assert all(Pin(config.FUTURE_CHANNELS[i][0]).value() == 0 for i in range(6))
        assert ticks_diff(ticks_ms(), controller.heartbeat) < 500
        if state['fans'][0]['warning']:
            seen_warning = True
            assert state['warning_count'] == 1, state
        elif seen_warning and state['fans'][0]['in_bounds']:
            recovered = True
            assert state['fans'][0]['out_of_bounds_s'] == 0
            break
    assert seen_warning and recovered, controller.snapshot()
    print('CORE1_WARNING_RECOVERY_OK warning appeared and cleared during same run')
    print('SIX_DRIVERS_STAY_ARMED_OK all PWM LOW throughout; no channel excluded')
finally:
    try:
        if controller is not None:
            controller.close()
    finally:
        PioFan.sample = original_sample
print('RPM_WARNING_RUNTIME_BOARD_OK saved files untouched; no positive-power commands')
