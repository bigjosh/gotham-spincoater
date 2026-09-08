"""PIO fan PWM that only sinks low or releases the signal.

For the Pico 2 W's RP2350 FT-capable digital GPIOs. This is not a
voltage translator for an RP2040 Pico or an analog-capable GPIO. The
Pico must remain powered while an external fan pull-up is energized.
Connect fan power last and disconnect it first. Never connect fan 12 V
to this signal. The fan supplies the PWM signal's pull-up.

Adapted from MicroPython's MIT-licensed examples/rp2/pio_pwm.py:
https://github.com/micropython/micropython/blob/v1.29.0/examples/rp2/pio_pwm.py
See THIRD_PARTY_NOTICES.md. side_pindir=True changes pin direction;
the PIO output value is initialized to zero and never set high.
"""

from machine import Pin, freq as cpu_frequency
from rp2 import PIO, StateMachine, asm_pio


@asm_pio(sideset_init=PIO.OUT_LOW, side_pindir=True)
def _open_drain_program():
    # Direction 1 = sink low; direction 0 = input/high impedance.
    # An empty nonblocking pull copies X to OSR, retaining current duty.
    pull(noblock).side(1)
    mov(x, osr)
    mov(y, isr)
    label("count")
    jmp(x_not_y, "skip")
    nop().side(0)
    label("skip")
    jmp(y_dec, "count")


class OpenDrainPWM:
    """One PIO state machine per fan, initially stopped and held low.

    set_duty(percent) takes the fan's positive/high duty, 0..100.
    0 is constant low; 100 is a constant high-impedance release. Middle
    values have about 0.1 percentage point resolution, clipped to the
    program's 0.1..99.8% range. `actual_duty` reports that quantization.

    Changes briefly hold low while resetting/preloading the state
    machine. This intentionally avoids stale FIFO duty values across
    stop/resume. Steady-state output runs autonomously at `frequency` Hz.
    Do not change machine.freq() while a waveform is running.
    """

    _MAX_COUNT = 997
    # For an intermediate duty, there are 3 setup instructions, two
    # instructions for each Y=997..0, and one matching NOP: 2000 clocks.
    _PERIOD_CYCLES = 2 * _MAX_COUNT + 6

    def __init__(self, pin=18, sm_id=0, freq=25000):
        if not isinstance(pin, int) or not 0 <= pin <= 22:
            raise ValueError("use a Pico 2 W digital header GPIO (0..22)")
        if not isinstance(sm_id, int) or not 0 <= sm_id <= 11:
            raise ValueError("RP2350 state machine id must be 0..11")
        if not isinstance(freq, int) or freq <= 0:
            raise ValueError("frequency must be a positive integer")
        self._pin = Pin(pin, Pin.OUT, pull=None, value=0)
        self._sm_clock = freq * self._PERIOD_CYCLES
        clock = cpu_frequency()
        # Match MicroPython RP2's 16.8-bit divider truncation.
        divider256 = clock * 256 // self._sm_clock
        if not 256 <= divider256 <= 65536 * 256:
            raise ValueError("requested PWM frequency is out of PIO range")
        self.frequency = clock * 256 / (divider256 * self._PERIOD_CYCLES)
        self._sm = StateMachine(
            sm_id, _open_drain_program, freq=self._sm_clock,
            sideset_base=self._pin)
        self._sm.active(0)
        self._pin.init(Pin.OUT, pull=None, value=0)
        self._closed = False
        self.duty = 0
        self.actual_duty = 0

    def set_duty(self, percent):
        """Set 0..100% positive duty and return the actual quantized duty."""
        if self._closed:
            raise RuntimeError("PWM channel is closed")
        percent = float(percent)
        if not 0 <= percent <= 100:
            raise ValueError("duty must be between 0 and 100 percent")
        if percent == self.duty:
            return self.actual_duty

        self._sm.active(0)
        self._pin.init(Pin.OUT, pull=None, value=0)
        if percent == 0:
            actual = 0
        elif percent == 100:
            self._pin.init(Pin.IN, pull=None)
            actual = 100
        else:
            # The released interval is exactly 2*X+2 PIO clocks.
            count = int(percent * self._PERIOD_CYCLES / 200 + 0.5) - 1
            count = max(0, min(self._MAX_COUNT, count))
            # init() disables/resets the SM, resets its PC and clears its
            # FIFOs. Pin initialization also clears the PIO output latch.
            self._sm.init(_open_drain_program, freq=self._sm_clock,
                          sideset_base=self._pin)
            self._sm.put(self._MAX_COUNT)
            self._sm.exec("pull()")
            self._sm.exec("mov(isr, osr)")
            self._sm.put(count)
            self._sm.exec("pull()")
            self._sm.exec("mov(x, osr)")
            # TX FIFO is empty. The first and later nonblocking pulls
            # recover the preloaded X, rather than any old queued value.
            self._sm.active(1)
            actual = (2 * count + 2) * 100 / self._PERIOD_CYCLES
        self.duty = percent
        self.actual_duty = actual
        return actual

    def close(self):
        """Stop PIO and hold the fan PWM signal low; safe to call twice."""
        self._sm.active(0)
        self._pin.init(Pin.OUT, pull=None, value=0)
        self.duty = 0
        self.actual_duty = 0
        self._closed = True
