"""Hardware STOP forces PWM pads LOW without interrupting any tach sampler.

SM11 waits for GP14, then a one-shot DMA chain sets each PWM GPIO's OUTOVER
LOW bit. The watcher stays latched after button release. Only explicit START
re-arms it, while every output remains forced LOW.
"""
from array import array
from machine import Pin, mem32
from rp2 import DMA, PIO, StateMachine
from combined_program import combined_program, PIO_CLOCK_HZ, GUARD_PC, GUARD_FIRED_PCS
from pio_fan import PioFan


class StopGuard:
    def __init__(self, fans, stop_pin=14, sm_id=11):
        if stop_pin != 14 or sm_id != 11:
            raise ValueError('STOP guard requires GP14 and PIO2 SM11')
        if sm_id in PioFan._claimed_sms:
            raise ValueError('STOP guard state machine is already claimed')
        self.fans = tuple(fans)
        if not self.fans:
            raise ValueError('STOP guard requires at least one fan')
        self.pin = Pin(stop_pin, Pin.IN, Pin.PULL_UP)
        self._sm_id = sm_id
        self._addr = 0x50400000 + 0xd4 + 3 * 0x18
        self._mask = array('I', [1 << 13])
        self._dmas = []
        self._armed = False
        self._closed = False
        self._sm = None
        PioFan._claimed_sms.add(sm_id)
        try:
            self._sm = StateMachine(sm_id)
            self._sm.active(0)
            for fan in self.fans:
                fan.stop()
                fan.guard = self
        except BaseException:
            self.close()
            raise

    def _release_dmas(self):
        # Clear EVERY link's EN before aborting/closing ANY link, otherwise a
        # still-running predecessor could trigger a channel during teardown.
        for dma in self._dmas:
            dma.active(0)
        first_error = None
        for dma in self._dmas:
            try:
                dma.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        self._dmas = []
        if first_error is not None:
            raise first_error

    def arm(self):
        if self._closed:
            raise RuntimeError('STOP guard is closed')
        self._armed = False
        for fan in self.fans:
            fan.stop()
        self._sm.active(0)
        self._release_dmas()
        try:
            # No OUT/SET/IN/JMP pin arguments: initializing this dedicated
            # watcher must not remux or reset any live sampler GPIO.
            self._sm.init(combined_program, freq=PIO_CLOCK_HZ)
            PioFan._loaded_blocks.add(2)
            if len(combined_program[0]) != 32 or mem32[self._addr] != 0:
                raise RuntimeError('STOP guard program must have origin zero')
            self._sm.put(self._mask[0])
            self._sm.exec(0x80a0)  # pull(block)
            self._sm.exec(0xa0c7)  # mov(isr, osr); PUSH clears it after firing.
            self._sm.exec(GUARD_PC)
            for _ in self.fans:
                self._dmas.append(DMA())
            # Configure followers first. Only the first channel is initially
            # triggered; it then waits for the watcher's RX FIFO DREQ.
            for i in range(len(self._dmas) - 1, -1, -1):
                dma = self._dmas[i]
                next_channel = (self._dmas[i + 1].channel if i + 1 < len(self._dmas)
                                else dma.channel)  # Self means no further chain.
                control = dma.pack_ctrl(size=2, inc_read=False, inc_write=False,
                                        treq_sel=23 if i == 0 else 0x3f,
                                        chain_to=next_channel, high_pri=True, irq_quiet=True)
                dma.config(read=self._sm if i == 0 else self._mask,
                           write=self.fans[i]._gpio_ctrl + 0x2000,
                           count=1, ctrl=control, trigger=i == 0)
            self._armed = True
            self._sm.active(1)
            return not self.fired()
        except BaseException:
            self._armed = False
            self._sm.active(0)
            self._release_dmas()
            for fan in self.fans:
                fan.stop()
            raise

    def fired(self):
        # The first DMA drains RX immediately, so FIFO occupancy is not a
        # reliable latch. PCs30/31 persist until the watcher is re-armed.
        return (not self._armed or not self.pin.value()
                or mem32[self._addr] in GUARD_FIRED_PCS)

    def close(self):
        if self._closed:
            return
        self._armed = False
        for fan in self.fans:
            fan.stop()
        if self._sm is not None:
            self._sm.active(0)
        self._release_dmas()
        self._closed = True
        PioFan._claimed_sms.discard(self._sm_id)
        if (2 in PioFan._loaded_blocks
                and not any(sm // 4 == 2 for sm in PioFan._claimed_sms)):
            PIO(2).remove_program(combined_program)
            PioFan._loaded_blocks.discard(2)
