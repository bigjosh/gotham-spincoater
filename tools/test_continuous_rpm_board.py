"""Zero-power board check for idle/disabled/STOP tach monitoring.

Runs all six real PIO/DMA drivers, verifies their passive mode and raw LOW
pads, and feeds scripted RPM into the real core-1/display pipeline. Scripted
RPM is NOT a physical tach measurement. No recipe starts or saved file is
accessed. Run through guarded pico.ps1 Exec; reboot the normal app afterward.
"""
from machine import Pin
from time import sleep_ms, ticks_ms, ticks_diff
from recipes import RecipeBook
from runtime import Controller
from pio_fan import PioFan
import config


class TestSelection:
    source = 'continuous RPM zero-power check'
    load_error = None

    def load(self):
        return (0,)


class NeverStart:
    def pressed(self, now):
        return False

    def block_until_release(self):
        pass


if not config.AUX_LINKS_DISCONNECTED:
    raise RuntimeError('All-six check requires confirmed GPIO modifications')

original_sample = PioFan.sample
scripted_rpm = [59, 60, 240, None, 750, 900]


def sample_with_scripted_rpm(self):
    result = original_sample(self)
    channel = config.TACH_STATE_MACHINES.index(self._sm_id)
    rpm = scripted_rpm[channel]
    # Keep real hardware errors and STOP state intact.
    result.update(rpm=rpm, valid=rpm is not None, age_ms=0)
    return result


def await_readings(controller, expected, stopped=False):
    began = ticks_ms()
    while ticks_diff(ticks_ms(), began) < 1500:
        sleep_ms(50)
        state = controller.snapshot()
        assert not controller.finished and not controller.closing, controller.error
        assert not state['running'], state
        assert all(fan['duty'] == 0 for fan in state['fans']), state
        assert all(not fan['driver_error'] for fan in state['fans']), state
        assert all(controller.fans[i].pwm_is_low() for i in range(6)), 'PWM pad not LOW'
        assert ticks_diff(ticks_ms(), controller.heartbeat) < 500
        actual = [fan['display_rpm'] for fan in state['fans']]
        if (actual == expected and all(controller.fans[i]._monitoring for i in range(6))
                and (not stopped or state['state'] == 'STOPPED')):
            return state
    raise AssertionError(controller.snapshot())


controller = None
try:
    controller = Controller(RecipeBook(), fan_settings=TestSelection())
    controller.start_button = NeverStart()
    PioFan.sample = sample_with_scripted_rpm
    controller.start_thread()
    await_readings(controller, [0, 60, 240, 0, 750, 900])
    print('IDLE_DISABLED_RPM_OK six passive samplers; threshold boundary 59/60')
    # Assert the existing shared active-LOW STOP line; never drive it HIGH.
    controller.stop_pin.init(Pin.OUT, value=0)
    scripted_rpm[:] = [125, 150, 250, 350, 450, 550]
    await_readings(controller, scripted_rpm, stopped=True)
    scripted_rpm[:] = [225, 260, 360, 460, 560, 660]
    await_readings(controller, scripted_rpm, stopped=True)
    print('HELD_STOP_RPM_OK fresh scripted readings; all raw PWM pads actively LOW')
finally:
    try:
        if controller is not None:
            controller.close()
    finally:
        PioFan.sample = original_sample
print('CONTINUOUS_RPM_BOARD_OK no positive power or saved-file changes')
