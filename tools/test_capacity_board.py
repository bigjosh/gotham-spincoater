"""Bounded synthetic six-PIO/DMA capture + Wi-Fi capacity test (~3 seconds).

Run only after stopping the application, with GP0/GP1 otherwise unwired.
GP18 (the actual fan PWM) is never configured or written by this script.

All six PIO state machines use GP0 as a DUMMY PWM output and observe GP1,
which hardware PWM drives at 100 Hz. Only the GPIO-selected PIO block drives
the physical GP0 pad. Therefore this checks six capture engines, six DMA
channels plus the shared STOP watcher/DMA chain and Wi-Fi; it does NOT verify
six independent physical output waveforms or six actual motors.

This test-only subclass gives each instance its own dummy-pin claim set.
Production PioFan's duplicate-pin checks and shared SM/program claims remain
unchanged. Wi-Fi uses PIO1; fans use SM0/1/2/3/8/9 in PIO0/PIO2.
"""
from machine import Pin, PWM, mem32
import network
from time import ticks_ms, ticks_diff, sleep_ms
from pio_fan import PioFan
from stop_guard import StopGuard


class CapacityFan(PioFan):
    def __init__(self, sm_id):
        # Only permit the intentional GP0/GP1 sharing in this test object.
        self._claimed_pins = set()
        super().__init__(pwm_pin=0, tach_pin=1, sm_id=sm_id, stop_pin=14)


readers = []
guard = None
source = None
ap = None
ap_owned = False
pins_owned = False
started = ticks_ms()
try:
    assert not PioFan._claimed_sms, 'Stop the application before the capacity test'
    ap = network.WLAN(network.WLAN.IF_AP)
    assert not ap.active(), 'Stop the existing AP before this bounded test'
    ap_owned = True
    ap.config(ssid='Gotham Spinner Test', security=0, key='', channel=6)
    Pin(14, Pin.IN, Pin.PULL_UP)
    pins_owned = True
    for sm_id in (0, 1, 2, 3, 8, 9):
        readers.append(CapacityFan(sm_id))
    assert all(reader._block != 1 for reader in readers), 'Fan claimed wireless PIO1'
    guard = StopGuard(tuple(readers))
    assert guard.arm()
    for reader in readers:
        assert reader.arm()

    # Configure GP1 after all tach Pin constructors so the PWM function remains
    # selected; PIO observes the pad independently of its GPIO output function.
    source = PWM(Pin(1), freq=100, duty_u16=32768)
    for reader, duty in zip(readers, (0, 25, 50, 75, 99, 100)):
        assert reader.set_duty(duty) == duty
    ap.active(True)
    ready_deadline = ticks_ms() + 1500
    while not ap.active() and ticks_diff(ready_deadline, ticks_ms()) > 0:
        sleep_ms(20)
    assert ap.active(), 'Wi-Fi AP did not become active'

    # Radio startup may have delayed Python long enough to fill RX FIFO.
    # Drain/reset that backlog, then test bounded 20 ms polling independently.
    for reader in readers:
        reader.sample()
    baseline_overflows = [reader._overflows for reader in readers]
    deadline = ticks_ms() + 1200
    next_poll = ticks_ms()
    readings = None
    while ticks_diff(deadline, ticks_ms()) > 0:
        readings = [reader.sample() for reader in readers]
        assert all(not value['fault'] for value in readings), 'PIO command fault'
        assert all(not value['stopped'] for value in readings), 'Unexpected STOP latch'
        next_poll += 20
        remaining = ticks_diff(next_poll, ticks_ms())
        if remaining > 0:
            sleep_ms(remaining)
    assert ap.active(), 'Wi-Fi AP stopped while six channels were active'
    for index, value in enumerate(readings):
        assert value['valid'], 'Channel %d did not acquire tach' % index
        assert abs(value['rpm'] - 3000) < 5, 'Channel %d has incorrect RPM' % index
        assert value['samples'] == 8, 'Channel %d did not fill averaging window' % index
        assert value['overflows'] == baseline_overflows[index], 'Channel %d polling overflowed' % index
    print('CAPACITY_CAPTURE six_rpm=%s AP=%s PIO1_enabled=0x%x' % (
        [round(value['rpm'], 1) for value in readings], ap.active(),
        mem32[0x50300000] & 15))

    # PIO must park before any sample()/stop() method gets a chance to help.
    Pin(14, Pin.OUT, value=0)
    sleep_ms(20)
    assert guard.fired(), 'Hardware STOP did not latch'
    assert all(reader._sm.active() for reader in readers), 'STOP interrupted tach capture'
    assert Pin(0).value() == 0, 'Dummy output did not go LOW'
    Pin(14, Pin.IN, Pin.PULL_UP)
    sleep_ms(5)
    assert guard.fired(), 'STOP release cleared the latch'
    assert all(reader.sample()['stopped'] for reader in readers), 'STOP latch not reported'
    print('CAPACITY_STOP six hardware latches stayed LOW after release; AP=%s' % ap.active())
finally:
    # Continue every cleanup even if one resource raises; leave the first
    # test exception intact instead of hiding it behind cleanup diagnostics.
    cleanup_errors = []
    for reader in readers:
        try:
            reader.close()
        except BaseException as error:
            cleanup_errors.append(('fan', repr(error)))
    if guard is not None:
        try:
            guard.close()
        except BaseException as error:
            cleanup_errors.append(('guard', repr(error)))
    if source is not None:
        try:
            source.deinit()
        except BaseException as error:
            cleanup_errors.append(('source', repr(error)))
    if ap_owned:
        try:
            ap.active(False)
        except BaseException as error:
            cleanup_errors.append(('ap', repr(error)))
    if pins_owned:
        for gpio in (0, 1):
            try:
                Pin(gpio, Pin.OUT, value=0)
                Pin(gpio, Pin.IN, pull=None)
            except BaseException as error:
                cleanup_errors.append(('gpio%d' % gpio, repr(error)))
        try:
            Pin(14, Pin.IN, Pin.PULL_UP)
        except BaseException as error:
            cleanup_errors.append(('stop_gpio', repr(error)))
    if cleanup_errors:
        print('CAPACITY_CLEANUP_ERRORS', cleanup_errors)
    else:
        print('CAPACITY_CLEANUP_COMPLETE DMA and fan programs released, AP off, GP0/1 inputs, STOP released')

assert not cleanup_errors, 'Capacity test resource cleanup failed'
print('CAPACITY_PASS six combined PIO/DMA engines + active Wi-Fi; elapsed_ms=%d; shared dummy output only' %
      ticks_diff(ticks_ms(), started))
