"""Pico 2 W fan bench: joystick duty adjustment and live tachometer."""
from machine import Pin, ADC
from time import ticks_ms, ticks_diff, sleep_ms
import gc
import sys
import config
from rig import FanRig
from display import Display
from ui import BenchUI


class Button:
    def __init__(self, gpio):
        self.pin = Pin(gpio, Pin.IN, Pin.PULL_UP)
        self.raw = self.pin.value()
        self.stable = self.raw
        self.changed = ticks_ms()

    def pressed(self, now):
        value = self.pin.value()
        if value != self.raw:
            self.raw = value
            self.changed = now
        if value != self.stable and ticks_diff(now, self.changed) >= 30:
            self.stable = value
            return value == 0
        return False


def direction(value, center):
    if value < center - 12000:
        return -1
    if value > center + 12000:
        return 1
    return 0


def run(duration_ms=None):
    # Establish stopped output before screen initialization or joystick setup.
    fan = FanRig()
    running = False
    requested = 0
    screen = None
    try:
        display = Display(rotation=config.DISPLAY_ROTATION,
                          inversion=config.DISPLAY_INVERSION, bgr=config.DISPLAY_BGR)
        screen = BenchUI(display, pwm_hz=config.PWM_HZ,
                         pulses_per_rev=config.PULSES_PER_REV,
                         push_pull=config.PWM_PUSH_PULL,
                         synchronous=config.SYNCHRONOUS_TACH,
                         channels=len(config.ENABLED_CHANNELS))
        start_button = Button(config.BUTTON_START)
        stop_button = Button(config.BUTTON_STOP)
        joystick_x = ADC(config.JOYSTICK_X)
        joystick_y = ADC(config.JOYSTICK_Y)
        # Use a fixed neutral band: a joystick held during boot must not become
        # the new center and then ramp the setting when released.
        center_x = 32768
        center_y = 32768
        last_direction = (0, 0)
        last_repeat = ticks_ms()
        repeat_interval = 350
        started = ticks_ms()
        last_display = started - 250
        last_report = started - 1000
        stats = fan.sample()
        screen.update(requested, running, stats)
        print('FAN_UI_READY PWM=GP%d TACH=GP%d MODE=%s HZ=%d OUTPUT=0%%' %
              (config.PWM_PIN, config.TACH_PIN,
               'PUSH_PULL' if config.PWM_PUSH_PULL else 'OPEN_DRAIN', config.PWM_HZ))
        print('TACH_SYNC=%s CHANNELS=%s AVERAGE_PERIODS=%d' %
              (config.SYNCHRONOUS_TACH, config.ENABLED_CHANNELS, config.PERIOD_AVERAGE))
        while duration_ms is None or ticks_diff(ticks_ms(), started) < duration_ms:
            now = ticks_ms()
            stop_event = stop_button.pressed(now)
            start_event = start_button.pressed(now)
            # BTN2 takes precedence and continuously inhibits output while held.
            if stop_event or not stop_button.pin.value():
                running = False
                requested = 0
                fan.set_duty(0)
            elif start_event:
                running = not running
                fan.set_duty(requested if running else 0)

            x = direction(joystick_x.read_u16(), center_x) * config.JOYSTICK_X_SIGN
            y = direction(joystick_y.read_u16(), center_y) * config.JOYSTICK_Y_SIGN
            stick = (x, y)
            change = y * 5 if y else x
            if change and stop_button.pin.value():
                if stick != last_direction or ticks_diff(now, last_repeat) >= repeat_interval:
                    requested = max(0, min(100, requested + change))
                    if running:
                        fan.set_duty(requested)
                    repeat_interval = 350 if stick != last_direction else 90
                    last_repeat = now
            else:
                repeat_interval = 350
            last_direction = stick

            if ticks_diff(now, last_display) >= 250:
                stats = fan.sample()
                screen.update(requested, running, stats)
                last_display = now
            if ticks_diff(now, last_report) >= 1000:
                for channel, values in stats['channels'].items():
                    print('FAN%d duty=%d rpm=%.1f hz=%.2f peak=%.1f periods=%d valid=%s sample_us=%.1f' %
                          (channel, requested if running else 0, values['rpm'], values['hz'],
                           values['peak_rpm'], values['pulses'], values['valid'],
                           values.get('sample_offset_us', 0)))
                last_report = now
            sleep_ms(10)
    except KeyboardInterrupt:
        print('Fan bench interrupted; output stopped.')
    except Exception as error:
        fan.set_duty(0)
        sys.print_exception(error)
        if screen is not None:
            screen.field('state', 'ERROR', 350, 22, 116, 16, 0xF800, 2)
        raise
    finally:
        fan.close()
        gc.collect()


if __name__ == '__main__':
    run()
