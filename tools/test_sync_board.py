"""Synthetic six-channel check on spare GP0/GP1 only; no fan is driven.

GP1 hardware PWM supplies 10 kHz wrap pacing. A PIO generator drives GP0
with 100 Hz tach plus narrow disturbances at both edges of that PWM. Six
independent PIO/DMA tach readers observe GP0, exercising shared-wrap pacing.
Do not run if other hardware has been connected to GP0/GP1.
"""
from machine import Pin, PWM
from rp2 import PIO, StateMachine, asm_pio
from time import sleep_ms
from sync_tach import SyncTachometer


@asm_pio(set_init=PIO.OUT_LOW)
def noisy_tach():
    wrap_target()
    mov(x, isr)
    label('high')
    wait(0, pin, 0)
    wait(1, pin, 0)
    set(pins, 0)[2]
    set(pins, 1)
    wait(0, pin, 0)
    set(pins, 0)[2]
    set(pins, 1)
    jmp(x_dec, 'high')
    mov(x, isr)
    label('low')
    wait(0, pin, 0)
    wait(1, pin, 0)
    set(pins, 1)[2]
    set(pins, 0)
    wait(0, pin, 0)
    set(pins, 1)[2]
    set(pins, 0)
    jmp(x_dec, 'low')
    wrap()


@asm_pio(set_init=PIO.OUT_LOW)
def clean_tach():
    # ISR=4997 gives exactly 5000 instruction clocks per half-period.
    wrap_target()
    set(pins, 1)
    mov(x, isr)
    label('clean_high')
    jmp(x_dec, 'clean_high')
    set(pins, 0)
    mov(x, isr)
    label('clean_low')
    jmp(x_dec, 'clean_low')
    wrap()


clock_pwm = None
generator = None
generator_program = noisy_tach
readers = []
try:
    tach = Pin(0, Pin.IN, Pin.PULL_UP)
    clock_pwm = PWM(Pin(1), freq=10000, duty_u16=16384)
    for sm_id in (0, 1, 2, 3, 8, 9):
        readers.append(SyncTachometer(clock_pwm, 1, tach, sm_id=sm_id))
    generator = StateMachine(4, noisy_tach, freq=1000000,
                             in_base=Pin(1), set_base=Pin(0))
    # SET immediates are limited to 0..31; preload the 50-cycle half-period.
    generator.put(49)
    generator.exec('pull()')
    generator.exec('mov(isr, osr)')
    generator.active(1)
    for duty in (16384, 32768, 49151):
        for reader in readers:
            reader.set_duty_u16(duty)
        for _ in range(3):
            sleep_ms(200)
            readings = [reader.sample() for reader in readers]
        for index, value in enumerate(readings):
            print('SYNTHETIC_SYNC%d duty=%d' % (index, duty), value)
            assert value['valid'] and abs(value['rpm'] - 3000) < 5, 'Wrong noisy tach period'
            assert value['samples'] == 8, 'Averaging window did not fill'
    generator.active(0)
    Pin(0, Pin.OUT, value=0)
    sleep_ms(1600)
    assert all(not reader.sample()['valid'] for reader in readers), 'Stale RPM survived timeout'
    # Independent 100 Hz source checks actual period capture at constant PWM.
    PIO(1).remove_program(noisy_tach)
    generator = None
    generator_program = clean_tach
    generator = StateMachine(4, clean_tach, freq=1000000, set_base=Pin(0))
    generator.put(4997)
    generator.exec('pull()')
    generator.exec('mov(isr, osr)')
    generator.active(1)
    for duty in (0, 65535):
        for reader in readers:
            reader.set_duty_u16(duty)
        sleep_ms(250)
        for reader in readers:
            value = reader.sample()
            assert value['valid'] and abs(value['rpm'] - 3000) < 5, 'Endpoint capture stopped'
        print('SYNTHETIC_ENDPOINT duty=%d samplers=%d' % (duty, len(readers)))
    readers[0].close()
    sleep_ms(250)
    assert all(reader.sample()['valid'] for reader in readers[1:]), 'Closing one halted siblings'
    print('SYNC_SELFTEST_PASS six simultaneous samplers, noisy waveform, timeout, endpoints, shared cleanup')
finally:
    for reader in readers:
        reader.close()
    if generator is not None:
        generator.active(0)
        PIO(1).remove_program(generator_program)
    if clock_pwm is not None:
        clock_pwm.deinit()
    Pin(0, Pin.IN, pull=None)
    Pin(1, Pin.IN, pull=None)
