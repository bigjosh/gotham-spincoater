"""Tach sampling paced by this fan's hardware PWM wrap, entirely in hardware.

RP2350 only: PWM wrap DREQ feeds a delay token to PIO through DMA.
Channels sharing a PWM slice share one wrap-paced DMA chain. PIO samples once
per PWM cycle at the middle of its longer phase and
reports full falling-to-falling periods in PWM-cycle units. No per-sample
Python interrupt, additional GPIO, or change to the PWM pad input is needed.
"""
from array import array
from machine import freq as cpu_frequency
from rp2 import DMA, PIO, StateMachine
from periods import PeriodMeasurements
from sync_program import sync_program, PIO_CLOCK_HZ, SAMPLE_OVERHEAD_CYCLES


class _WrapPacer:
    """One consumer of a PWM DREQ, even when both PWM outputs are in use.

    Multiple DMA channels on the same DREQ are unsupported by RP2350. A single
    reader uses endless mode. For multiple readers the first DMA is wrap-paced,
    then a finite one-word chain fans out to the others and rearms the first.
    """

    def __init__(self, dreq):
        self.dreq = dreq
        self.readers = []

    def stop(self):
        # Disable every link BEFORE aborting any link: another DMA completion
        # must not re-trigger a channel during its abort (RP2350 erratum E5).
        for reader in self.readers:
            if reader._dma is not None:
                # active(False) aborts immediately; clear EN through the
                # non-triggering control alias first for the whole chain.
                reader._dma.ctrl = reader._dma.pack_ctrl(
                    default=reader._dma.ctrl, enable=False)
        for reader in self.readers:
            if reader._dma is not None:
                reader._dma.close()
                reader._dma = None
            if reader._sm is not None:
                reader._sm.active(0)
                reader._sm.irq(handler=None)

    def start(self):
        if not self.readers:
            return
        try:
            for reader in self.readers:
                reader.measurements.record(0)
                reader._first = True
                reader._sm.init(sync_program, freq=PIO_CLOCK_HZ,
                                jmp_pin=reader._tach)
                SyncTachometer._loaded_blocks.add(reader._sm_id // 4)
                reader._sm.irq(handler=reader._handler, hard=True)
                reader._sm.active(1)
                reader._dma = DMA()
            size = len(self.readers)
            for index, reader in enumerate(self.readers):
                next_dma = self.readers[(index + 1) % size]._dma
                ctrl = reader._dma.pack_ctrl(
                    size=2, inc_read=False, inc_write=False, high_pri=True,
                    irq_quiet=True, treq_sel=self.dreq if index == 0 else 63,
                    chain_to=next_dma.channel)
                reader._dma.config(read=reader._token, write=reader._sm,
                                   count=0xf0000001 if size == 1 else 1,
                                   ctrl=ctrl, trigger=False)
            self.readers[0]._dma.active(1)
        except BaseException:
            self.stop()
            for reader in self.readers:
                reader._pwm.duty_u16(0)
                reader._duty = None
            raise


class SyncTachometer:
    _claimed_sms = set()
    _pacers = {}
    _loaded_blocks = set()

    def __init__(self, pwm, pwm_pin, tach_pin, sm_id=0, window=8, timeout_ms=1500,
                 pulses_per_rev=2):
        if type(sm_id) is not int or not 0 <= sm_id < 12:
            raise ValueError('RP2350 PIO state machine must be 0..11')
        if sm_id in self._claimed_sms:
            raise ValueError('PIO state machine already used by another tach')
        if type(pwm_pin) is not int or not 0 <= pwm_pin <= 22:
            raise ValueError('PWM must use a digital Pico header GPIO')
        # Integer microsecond PWM periods keep the hard IRQ allocation-free.
        if pwm.freq() != 10000:
            raise ValueError('Synchronous bench mode requires 10000 Hz PWM')
        self.measurements = PeriodMeasurements(pulses_per_rev=pulses_per_rev,
                                               window=window, timeout_ms=timeout_ms)
        self._pwm = pwm
        self._tach = tach_pin
        self._sm_id = sm_id
        self._dreq = 32 + ((pwm_pin >> 1) & 7)  # RP2350 DREQ_PWM_WRAP0 = 32.
        self._token = array('I', [0])
        # get(array('H')) truncates in C: no large-int allocation in a hard IRQ.
        self._rx = array('H', [0])
        self._handler = self._on_period
        self._dma = None
        self._sm = None
        self._closed = False
        self._pacer = None
        self._first = True
        self._duty = None
        self.sample_offset_us = 50.0
        self._claimed_sms.add(sm_id)
        try:
            self._sm = StateMachine(sm_id)
            self._pacer = self._pacers.get(self._dreq)
            if self._pacer is None:
                self._pacer = _WrapPacer(self._dreq)
                self._pacers[self._dreq] = self._pacer
            self._pacer.stop()
            self._pacer.readers.append(self)
            self.set_duty_u16(pwm.duty_u16())
        except BaseException:
            self.close()
            raise

    def set_duty_u16(self, duty):
        if self._closed:
            raise RuntimeError('Tach sampler is closed')
        if duty == self._duty:
            return
        if type(duty) is not int or not 0 <= duty <= 65535:
            raise ValueError('Duty must be an integer from 0 to 65535')
        self._pacer.stop()
        self._pwm.duty_u16(duty)
        actual_duty = self._pwm.duty_u16()
        fraction = actual_duty / 65535
        self.sample_offset_us = 100 * (
            fraction / 2 if fraction >= 0.5 else (1 + fraction) / 2)
        # Hardware PWM starts HIGH at wrap. Even 0/100% duty still generates
        # wrap DREQs, so sampling continues at a virtual cycle midpoint.
        divider256 = cpu_frequency() * 256 // PIO_CLOCK_HZ
        pio_hz = cpu_frequency() * 256 / divider256
        self._token[0] = max(0, round(self.sample_offset_us * pio_hz / 1000000)
                             - SAMPLE_OVERHEAD_CYCLES)
        self._pacer.start()
        self._duty = duty

    def _on_period(self, sm):
        while sm.rx_fifo():
            sm.get(self._rx)
            cycles = self._rx[0]
            if self._first:
                # The first edge only establishes a baseline after startup or
                # any duty change; its partial startup period is not measured.
                self._first = False
            else:
                self.measurements.record(cycles * 100)

    def sample(self):
        reading = self.measurements.sample()
        reading['sample_offset_us'] = self.sample_offset_us
        return reading

    def close(self):
        if self._closed:
            return
        if self._pacer is not None:
            self._pacer.stop()
            if self in self._pacer.readers:
                self._pacer.readers.remove(self)
            if not self._pacer.readers:
                self._pacers.pop(self._dreq, None)
        self._claimed_sms.discard(self._sm_id)
        self._closed = True
        block = self._sm_id // 4
        if (block in self._loaded_blocks and
                not any(sm_id // 4 == block for sm_id in self._claimed_sms)):
            # Remove only our program, never another peripheral's allocation.
            PIO(block).remove_program(sync_program)
            self._loaded_blocks.discard(block)
        if self._pacer is not None:
            self._pacer.start()
