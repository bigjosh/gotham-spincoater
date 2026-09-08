"""Six-channel-capable PWM/tach/STOP driver, one PIO SM and DMA per fan.

Call sample() on the control core every ~20 ms. Only this core should call
driver methods; UI and HTTP consume controller snapshots. No Python hard IRQ
is used. Physical STOP and command DMA starvation latch LOW inside PIO.
"""
from array import array
from machine import Pin, freq as cpu_frequency, mem32
from rp2 import DMA, PIO, StateMachine
from time import ticks_diff, ticks_us
from periods import PeriodMeasurements
from combined_program import (combined_program, pack_command, PIO_CLOCK_HZ,
                              PERIOD_US, LOW_SAMPLE_PC, STOP_PC, STOP_PCS)


class PioFan:
    _claimed_sms = set()
    _claimed_pins = set()
    _loaded_blocks = set()

    def __init__(self, pwm_pin, tach_pin, sm_id, stop_pin=14, window=8,
                 pulses_per_rev=2, timeout_ms=1500):
        if type(sm_id) is not int or sm_id not in (0, 1, 2, 3, 8, 9, 10, 11):
            raise ValueError('Use PIO0/PIO2; PIO1 is reserved for wireless')
        if type(pwm_pin) is not int or not 0 <= pwm_pin <= 22:
            raise ValueError('PWM must use a digital GPIO from 0 to 22')
        if type(tach_pin) is not int or tach_pin not in tuple(range(23)) + (26, 27, 28):
            raise ValueError('Tach must use a Pico header GPIO')
        if type(stop_pin) is not int or not 0 <= stop_pin <= 22:
            raise ValueError('STOP must use a digital GPIO')
        if len({pwm_pin, tach_pin, stop_pin}) != 3:
            raise ValueError('PWM, tach and STOP must use different GPIOs')
        if sm_id in self._claimed_sms:
            raise ValueError('PIO state machine is already claimed')
        if pwm_pin in self._claimed_pins or tach_pin in self._claimed_pins:
            raise ValueError('Fan GPIO is already claimed')
        self._cpu_hz = cpu_frequency()
        if self._cpu_hz % PIO_CLOCK_HZ:
            raise ValueError('CPU frequency must be an integer multiple of 10 MHz')
        self._sm_id = sm_id
        self._block = sm_id // 4
        self._local = sm_id % 4
        # RP2350 addressmap.h / pio.h: PIO bases are 0x502/503/50400000;
        # SM_ADDR=0xd4 and EXECCTRL=0xcc with a 0x18-byte SM stride.
        self._base = 0x50200000 + self._block * 0x100000
        self._addr = self._base + 0xd4 + self._local * 0x18
        self._execctrl = self._base + 0xcc + self._local * 0x18
        self._debug = self._base + 8
        self._irq = self._base + 0x30
        self._bit = 1 << self._local
        self._debug_mask = self._bit * 0x01010101
        self._pins = (pwm_pin, tach_pin)
        self._command = array('I', [pack_command(0)[0]])
        self._rx = array('I', [0])
        self.measurements = PeriodMeasurements(pulses_per_rev=pulses_per_rev,
                                               window=window, timeout_ms=timeout_ms)
        self._timeout_us = timeout_ms * 1000
        self._last_poll_us = ticks_us()
        self._first = True
        self._duty = 0.0
        self._stopped = False
        self._closed = False
        self._fault = None
        self._overflows = 0
        self._sm = None
        self._initialized = False
        self._dma = None
        self._output = None
        self._claimed_sms.add(sm_id)
        self._claimed_pins.update(self._pins)
        try:
            self._output = Pin(pwm_pin, Pin.OUT, value=0)
            self._tach = Pin(tach_pin, Pin.IN, Pin.PULL_UP)
            self._stop_pin = Pin(stop_pin, Pin.IN, Pin.PULL_UP)
            self._sm = StateMachine(sm_id)
            self._sm.active(0)
            mem32[self._debug] = self._debug_mask
            mem32[self._irq] = self._bit
            # Boot at zero, including when STOP is already held down.
            if self._stop_pin.value():
                self.arm()
            else:
                self._stopped = True
        except BaseException:
            self.close()
            raise

    def _close_dma(self):
        if self._dma is not None:
            self._dma.close()
            self._dma = None

    def arm(self):
        """Explicitly restart at 0%; never reuse a previously queued command."""
        if self._closed:
            raise RuntimeError('Fan is closed')
        if not self._stop_pin.value():
            self.stop()
            return False
        self.stop()
        if cpu_frequency() != self._cpu_hz:
            self._fault = 'clock_changed'
            return False
        try:
            self._sm.init(combined_program, freq=PIO_CLOCK_HZ,
                          out_base=self._output, set_base=self._output,
                          in_base=self._stop_pin, jmp_pin=self._tach)
            self._initialized = True
            self._loaded_blocks.add(self._block)
            # A 32-word program can only be loaded at origin zero. Enforce the
            # assumption used by the computed state-dispatch instructions.
            if len(combined_program[0]) != 32 or mem32[self._addr] != 0:
                raise RuntimeError('Combined PIO program must have origin zero')
            # STATUS_SEL=TXLEVEL, STATUS_N=1. Other EXECCTRL fields unchanged.
            mem32[self._execctrl] = (mem32[self._execctrl] & ~0x7f) | 1
            # Preencoded instructions avoid formatting/allocation, and JMP
            # strings are unsupported by MicroPython's asm_pio_encode().
            self._sm.exec(0xa02b)  # mov(x, invert(null))
            self._sm.exec(0xe040 | LOW_SAMPLE_PC)  # set(y, LOW_SAMPLE_PC)
            self._sm.exec(0xa0c2)  # mov(isr, y)
            self._command[0] = pack_command(0)[0]
            for _ in range(4):
                self._sm.put(self._command[0])
            self._dma = DMA()
            control = self._dma.pack_ctrl(size=2, inc_read=False, inc_write=False,
                                          treq_sel=self._block * 8 + self._local,
                                          high_pri=True, irq_quiet=True)
            self._dma.config(read=self._command, write=self._sm,
                             # RP2350 DMA TRANS_COUNT.MODE=0xf is ENDLESS;
                             # the low count must be nonzero, hence ...0001.
                             # See SDK hardware/regs/dma.h, TRANS_COUNT_MODE.
                             count=0xf0000001, ctrl=control, trigger=True)
            mem32[self._debug] = self._debug_mask
            mem32[self._irq] = self._bit
            self.measurements.record(0)
            self._first = True
            self._last_poll_us = ticks_us()
            self._fault = None
            self._stopped = False
            self._sm.active(1)
            return True
        except BaseException:
            self.stop()
            raise

    def set_duty(self, percent):
        if self._closed:
            raise RuntimeError('Fan is closed')
        word, applied = pack_command(percent)
        # Also observe a latched brief button press, even after its release.
        if self._stopped or mem32[self._addr] in STOP_PCS or not self._stop_pin.value():
            self.stop()
            return 0.0
        self._command[0] = word  # Aligned, atomic 32-bit update for DMA.
        self._duty = applied
        return applied

    def sample(self):
        """Drain complete periods; return a control/UI snapshot.

        RX overflow drops samples in hardware. Discard the old queue and
        establish a fresh baseline rather than attaching today's time to it.
        """
        if self._closed:
            raise RuntimeError('Fan is closed')
        now = ticks_us()
        gap = ticks_diff(now, self._last_poll_us)
        self._last_poll_us = now
        if cpu_frequency() != self._cpu_hz:
            self._fault = 'clock_changed'
            self.stop()
        if mem32[self._irq] & self._bit:
            self._fault = 'command_underrun'
            self.stop()
        debug = mem32[self._debug]
        if debug & (self._bit << 24):
            # Guarded PULL should never stall; fail closed if it ever does.
            self._fault = 'command_stall'
            self.stop()
        if not self._stopped and (mem32[self._addr] in STOP_PCS or
                                   not self._stop_pin.value()):
            self.stop()
        overflow = bool(debug & self._bit)
        if overflow:
            self._overflows += 1
        reset = overflow or gap < 0 or gap >= self._timeout_us or self._stopped
        if reset:
            self.measurements.record(0)
            self._first = True
            # Bound the read to a snapshot: new periods can arrive concurrently.
            for _ in range(self._sm.rx_fifo()):
                self._sm.get(self._rx)
        else:
            for _ in range(self._sm.rx_fifo()):
                self._sm.get(self._rx)
                cycles = self._rx[0]
                if self._first:
                    self._first = False
                elif 0 < cycles <= self._timeout_us // PERIOD_US:
                    self.measurements.record(cycles * PERIOD_US)
                else:
                    self.measurements.record(0)
                    self._first = True
        mem32[self._debug] = debug & self._debug_mask
        result = self.measurements.sample()
        result.update(duty=self._duty, stopped=self._stopped,
                      fault=self._fault, overflow=overflow,
                      overflows=self._overflows)
        return result

    def stop(self):
        """Immediately drive LOW; only arm() can resume after this call."""
        if self._closed:
            return
        self._duty = 0.0
        self._stopped = True
        self._command[0] = pack_command(0)[0]
        # SIO takes the pad LOW before any DMA/resource cleanup can block.
        try:
            if self._output is not None:
                self._output.init(Pin.OUT, value=0)
        finally:
            try:
                if self._sm is not None:
                    self._sm.active(0)
                    if self._initialized:
                        self._sm.exec(0xe000)  # set(pins, 0)
                        self._sm.exec(STOP_PC)  # encoded unconditional JMP
            finally:
                try:
                    self._close_dma()
                finally:
                    self.measurements.record(0)
                    self._first = True

    def close(self):
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._closed = True
            self._claimed_sms.discard(self._sm_id)
            self._claimed_pins.difference_update(self._pins)
            if (self._block in self._loaded_blocks and
                    not any(sm // 4 == self._block for sm in self._claimed_sms)):
                PIO(self._block).remove_program(combined_program)
                self._loaded_blocks.discard(self._block)
