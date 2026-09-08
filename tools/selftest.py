"""Board-only bench self-test, without a fan or wire loopback.

Uses otherwise-unused GP0/GP1 and PIO SM10/11. Do not run after attaching
additional fan channels to those pins. Tests are labeled synthetic and are
never presented as fan speed measurements. The normal bench UI is restarted
by the host after the test.
"""
from machine import Pin, time_pulse_us
from time import sleep_ms
from fan import Fan
from open_drain_pwm import OpenDrainPWM

probe = None
source = None
try:
    source = OpenDrainPWM(pin=0, sm_id=11, freq=25000)
    source.set_duty(50)
    # Only set the pull; leave the PIO pin function/direction unchanged.
    Pin(0, pull=Pin.PULL_UP)
    sleep_ms(10)
    high = sorted(time_pulse_us(Pin(0), 1, 2000) for _ in range(11))[5]
    low = sorted(time_pulse_us(Pin(0), 0, 2000) for _ in range(11))[5]
    print('SYNTHETIC_PWM high_us=%d low_us=%d requested_hz=25000 actual_hz=%.2f' %
          (high, low, source.frequency))
    assert 16 <= high <= 24 and 16 <= low <= 24, 'PWM pulse widths unexpected'
    source.set_duty(0)
    assert Pin(0).value() == 0, '0 percent must stay low'
    source.set_duty(100)
    Pin(0, pull=Pin.PULL_UP)
    sleep_ms(1)
    assert Pin(0).value() == 1, '100 percent must release'
    source.close()

    # A PIO pulse source and GPIO IRQ can observe the same physical pad;
    # there is no external jumper and no signal is applied to GP18/GP19.
    probe = Fan(pwm_pin=1, tach_pin=0, sm_id=10)
    source = OpenDrainPWM(pin=0, sm_id=11, freq=100)
    source.set_duty(50)
    Pin(0, pull=Pin.PULL_UP)
    for _ in range(4):
        sleep_ms(250)
        sample = probe.sample()
    print('SYNTHETIC_TACH', sample)
    assert sample['valid'] and abs(sample['rpm'] - 3000) < 15, '100Hz must read 3000RPM'
    assert 90 <= sample['pulses'] <= 115, 'Unexpected tach edge count'
    source.close()
    sleep_ms(1600)
    sample = probe.sample()
    assert not sample['valid'] and sample['rpm'] == 0, 'Missing tach must expire'
    print('SELFTEST_PASS PWM endpoints, waveform, real GPIO IRQ tach, and timeout')
finally:
    if source is not None:
        source.close()
    if probe is not None:
        probe.close()
    Pin(0, Pin.IN, pull=None)
    Pin(1, Pin.IN, pull=None)
