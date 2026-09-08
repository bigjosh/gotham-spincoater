"""On-device test on unused GP0/GP1/GP20; run through tools/pico.ps1.

No wiring needed: the PWM peripheral drives the same GP1 input pad that
PIO reads. GP20 simulates the STOP button, leaving the kit button untouched.
The actual fan on GP18/19 remains at zero power throughout this test.
"""
import sys
sys.modules.pop('pio_fan', None)
from machine import Pin, PWM, time_pulse_us, mem32
from time import sleep_ms, ticks_ms, ticks_diff
from pio_fan import PioFan

fan = generator = None
try:
    fan = PioFan(0, 1, 0, stop_pin=20)
    stop = Pin(20, Pin.OUT, value=1)
    generator = PWM(Pin(1), freq=100, duty_u16=32768)

    def collect(duration=160):
        began = ticks_ms()
        while ticks_diff(ticks_ms(), began) < duration:
            value = fan.sample()
            assert not value['stopped'], value
            assert not value['overflow'], value
            sleep_ms(10)
        return value

    for duty in (0, 25, 50, 75, 100):
        fan.set_duty(duty)
        value = collect()
        assert value['valid'] and abs(value['rpm'] - 3000) < 20, value
        if duty in (0, 100):
            assert Pin(0).value() == (1 if duty else 0)
        else:
            # time_pulse_us counts only the remainder if called during a
            # pulse. Repeated same-level calls include complete next pulses.
            high = max(time_pulse_us(Pin(0), 1, 1000) for _ in range(6))
            low = max(time_pulse_us(Pin(0), 0, 1000) for _ in range(6))
            print('PULSE', duty, high, low)
            assert abs(high - duty) <= 3, (duty, high, low)
            assert abs(high + low - 100) <= 5, (duty, high, low)
        print('PWM/TACH', duty, value['rpm'], 'OK')

    for step in range(60):
        fan.set_duty((step * 7) % 101)
        value = collect(20)
        assert value['valid'] and abs(value['rpm'] - 3000) < 80, value
    print('Continuous duty changes preserve tach: OK')

    fan.set_duty(75)
    sleep_ms(2)
    stop.value(0)
    # No driver/Python servicing while PIO must stop autonomously.
    sleep_ms(20)
    assert Pin(0).value() == 0
    stop.value(1)
    sleep_ms(20)
    assert Pin(0).value() == 0
    value = fan.sample()
    assert value['stopped'] and not value['valid'], value
    assert fan.set_duty(100) == 0
    assert fan.arm()
    fan.set_duty(50)
    assert collect()['valid']
    print('STOP latches LOW until explicit arm: OK')

    fan._dma.active(False)
    sleep_ms(20)
    assert Pin(0).value() == 0
    value = fan.sample()
    assert value['stopped'] and value['fault'] == 'command_underrun', value
    print('Command DMA starvation latches LOW: OK')
    assert fan.arm()
    assert collect()['valid']

    # Intentionally overflow the four-word period FIFO; old data must be
    # invalidated, followed by recovery from new complete periods.
    sleep_ms(100)
    value = fan.sample()
    assert value['overflow'] and not value['valid'], value
    assert collect()['valid']
    print('Tach FIFO overflow discard/recovery: OK')
    print('COMBINED_BOARD_PASS')
finally:
    if fan is not None:
        fan.close()
    if generator is not None:
        generator.deinit()
    Pin(0, Pin.OUT, value=0)
    Pin(1, Pin.IN)
    Pin(20, Pin.IN)
