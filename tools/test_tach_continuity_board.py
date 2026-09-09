"""Exercise real six-channel PIO capture across START/STOP, at zero power.

Synthetic edges use RP2350 INOVER on the tach GPIOs. This changes only the
internal input seen by PIO: no tach lead is driven electrically. Every PWM
command is zero. Original input overrides and Python methods are restored;
saved recipes/selections are never accessed. Run through guarded pico.ps1
Exec after deployment, then reboot the normal application.
"""
from machine import Pin, mem32
from time import sleep_ms, ticks_ms, ticks_diff
from recipes import RecipeBook, validate_profile
from runtime import Controller
from pio_fan import PioFan
import config


class TestSelection:
    source = 'zero-power tach continuity test'
    load_error = None

    def load(self):
        return tuple(range(6))


class TestStart:
    pending = False

    def pressed(self, now):
        result = self.pending
        self.pending = False
        return result

    def block_until_release(self):
        self.pending = False


if not config.AUX_LINKS_DISCONNECTED:
    raise RuntimeError('All-six check requires confirmed GPIO modifications')

original_start_capture = PioFan._start_capture
capture_starts = {}


def counted_start_capture(self, *args):
    pin = self._pins[0]
    capture_starts[pin] = capture_starts.get(pin, 0) + 1
    return original_start_capture(self, *args)


controller = None
saved_inputs = []
level = False


def drive_internal_input(high):
    for address, original in saved_inputs:
        mem32[address] = (original & ~(3 << 16)) | ((3 if high else 2) << 16)


def observe(duration_ms, expected_running):
    global level
    began = ticks_ms()
    before = controller.snapshot()['fans']
    before_counts = [fan.get('pulses', 0) for fan in before]
    while ticks_diff(ticks_ms(), began) < duration_ms:
        level = not level
        drive_internal_input(level)
        sleep_ms(5)
        state = controller.snapshot()
        assert not controller.finished and not controller.closing, controller.error
        assert all(fan['duty'] == 0 and not fan['driver_error'] for fan in state['fans']), state
        assert all(controller.fans[i].pwm_is_low() for i in range(6)), 'PWM pad not LOW'
        assert all(fan['display_rpm'] >= 0 for fan in state['fans']), state
        assert ticks_diff(ticks_ms(), controller.heartbeat) < 500
    state = controller.snapshot()
    assert state['running'] == expected_running, state
    for i, fan in enumerate(state['fans']):
        assert fan['valid'] and fan['samples'] == config.PERIOD_AVERAGE, (i, fan)
        assert 1000 < fan['rpm'] < 4000, (i, fan)
        assert fan['period_us'] > 0, (i, fan)
        assert fan['pulses'] > before_counts[i] + 10, (i, before_counts[i], fan)
    assert capture_starts == initial_starts, ('Sampler restarted', capture_starts)
    return state


try:
    PioFan._start_capture = counted_start_capture
    controller = Controller(RecipeBook(), fan_settings=TestSelection())
    controller._profile = validate_profile({'name': 'Zero-power continuity', 'steps': [
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 30},
        {'rpm': 0, 'slew_s': 0, 'dwell_s': 0},
    ]})
    starter = TestStart()
    controller.start_button = starter
    for pwm, tach in config.FUTURE_CHANNELS:
        address = 0x40028000 + tach * 8 + 4
        saved_inputs.append((address, mem32[address]))
    initial_starts = dict(capture_starts)
    assert len(initial_starts) == 6 and all(count == 1 for count in initial_starts.values())
    # No worker exists yet: only PIO and DMA can react during this STOP hold.
    assert controller.stop_guard.arm()
    for fan in controller.fans.values():
        assert fan.arm()  # Always starts at zero; never issue positive duty.
        assert mem32[fan._gpio_ctrl] & (3 << 12) == 0
    controller.stop_pin.init(Pin.OUT, value=0)
    sleep_ms(10)
    assert controller.stop_guard.fired()
    for fan in controller.fans.values():
        assert mem32[fan._gpio_ctrl] & (3 << 12) == (2 << 12)
        assert fan.pwm_is_low() and fan._sm.active()
    controller.stop_pin.init(Pin.IN, Pin.PULL_UP)
    print('AUTONOMOUS_STOP_OK six hardware LOW overrides; all samplers still running; no worker')
    controller.start_thread()
    observe(600, False)
    print('IDLE_REAL_PIO_CAPTURE_OK six full period windows; all PWM LOW')

    starter.pending = True
    observe(400, True)
    print('START_PRESERVES_CAPTURE_OK no sampler restart or lost averaging window')

    controller.stop_pin.init(Pin.OUT, value=0)
    observe(400, False)
    print('HELD_STOP_PRESERVES_CAPTURE_OK new PIO periods continue; all PWM LOW')

    controller.stop_pin.init(Pin.IN, Pin.PULL_UP)
    observe(400, False)
    print('STOP_RELEASE_STAYS_STOPPED_OK tach continues without power restart')

    starter.pending = True
    observe(400, True)
    print('RESTART_PRESERVES_CAPTURE_OK six samplers initialized exactly once')
finally:
    try:
        if controller is not None:
            controller.close()
    finally:
        for address, original in saved_inputs:
            mem32[address] = original
        PioFan._start_capture = original_start_capture
print('TACH_CONTINUITY_BOARD_OK synthetic internal edges; no positive PWM or saved-file changes')
