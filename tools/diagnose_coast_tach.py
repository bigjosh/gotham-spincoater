"""Measure actual fan #0 tach before/after PWM LOW; no saved file changes.

Ten seconds at zero before a four-second 30% PWM pulse. All other fans stay
LOW. Physical STOP remains latched in hardware throughout. No tach input
overrides, remuxing, or generated pulses are used. Reboot the app afterward.
"""
from machine import Pin, mem32
from time import ticks_ms, ticks_diff, sleep_ms
from recipes import RecipeBook
from runtime import Controller
from display import Display
import config


class NoSelection:
    source = 'physical coast diagnostic'
    load_error = None

    def load(self):
        return ()


controller = None
last_level = None
edges = 0
falling = 0
rx_words = 0
last_report = 0
phase = 'WAIT'


def observe(duration_ms):
    global last_level, edges, falling, rx_words, last_report
    began = ticks_ms()
    while ticks_diff(ticks_ms(), began) < duration_ms:
        now = ticks_ms()
        raw = mem32[0x40028000 + 19 * 8]
        level = (raw >> 17) & 1
        if last_level is not None and level != last_level:
            edges += 1
            falling += level == 0
        last_level = level
        if controller.stop_guard.fired():
            for driver in controller.fans.values():
                driver.stop()
        readings = {}
        for i, driver in controller.fans.items():
            if i == 0:
                rx_words += driver._sm.rx_fifo()
            readings[i] = driver.sample()
        fan = readings[0]
        driver = controller.fans[0]
        assert all(controller.fans[i].pwm_is_low() for i in range(1, 6))
        if phase != 'RUN' or controller.stop_guard.fired():
            assert driver.pwm_is_low()
        if ticks_diff(now, last_report) >= 100:
            last_report = now
            print('COAST', phase, 't', now, 'level', level, 'edges', edges,
                  'falling', falling, 'rx_words', rx_words, 'last_cycles', driver._rx[0],
                  'rpm', fan['rpm'], 'valid', fan['valid'], 'samples', fan['samples'],
                  'pulses', fan['pulses'], 'age', fan['age_ms'],
                  'error', fan['fault'], 'overflow', fan['overflows'],
                  'pc', mem32[driver._addr], 'active', driver._sm.active(),
                  'starts', driver.capture_starts, 'pwm', driver._duty,
                  'guard', controller.stop_guard.fired())
        sleep_ms(1)


try:
    controller = Controller(RecipeBook(), fan_settings=NoSelection())
    display = Display(rotation=config.DISPLAY_ROTATION,
                      inversion=config.DISPLAY_INVERSION, bgr=config.DISPLAY_BGR)
    display.fill(0)
    display.text('FAN #0 TACH TEST', 32, 50, 0xffff, scale=3)
    display.text('STARTS IN 10 SECONDS', 32, 110, 0xffff, scale=2)
    display.text('KEEP HANDS CLEAR', 32, 150, 0xffff, scale=2)
    display.text('STOP BUTTON ACTIVE', 32, 190, 0xffff, scale=2)
    observe(10000)
    if controller.stop_guard.fired():
        raise RuntimeError('Physical STOP cancels diagnostic; no motor run')
    fan = controller.fans[0]
    assert fan.arm()
    phase = 'RUN'
    display.fill(0)
    display.text('FAN #0: 30% FOR 4s', 24, 100, 0xffff, scale=2)
    display.text('STOP BUTTON ACTIVE', 24, 150, 0xffff, scale=2)
    fan.set_duty(30)
    observe(4000)
    fan.set_duty(0)
    phase = 'ZERO_DUTY'
    display.fill(0)
    display.text('PWM LOW: OBSERVING TACH', 16, 100, 0xffff, scale=2)
    observe(1000)
    fan.stop()
    phase = 'DISABLED'
    observe(1000)
    controller.stop_pin.init(Pin.OUT, value=0)
    phase = 'HARDWARE_STOP'
    observe(3000)
finally:
    if controller is not None:
        controller.close()
print('COAST_DIAGNOSTIC_DONE all PWM LOW; saved files untouched')
