"""Continuous RP2350 PIO PWM/tach with a shared hardware STOP watcher.

The 32 words occupy a whole PIO instruction memory, so computed jumps have
origin zero. PioFan initializes X and ISR with injected instructions before
starting. JMP PIN selects this fan's tach. Words 29-31 are a separate STOP
watcher, used only by SM11 to trigger GPIO-override DMA without halting tach.

At 10 MHz every normal cycle lasts 1000 clocks. TX DMA supplies an atomic
30-bit command: three output levels and three nine-bit delays. The cycle is
centred on the longer phase, with tach sampled at its midpoint. Both sampled
levels and both transitions have equal execution time.

STATUS must be configured as TX FIFO level < 1. An empty command FIFO sets
this SM's hardware IRQ flag and parks LOW, rather than blocking before STOP.
No Python interrupt handler is required for either periods or this fault flag.
"""
from rp2 import PIO, asm_pio

PIO_CLOCK_HZ = 10_000_000
PWM_HZ = 10_000
CYCLE_CLOCKS = 1000
PERIOD_US = 100
LOW_SAMPLE_PC = 17
HIGH_SAMPLE_PC = 22
STOP_PC = 3
STOP_PCS = (3, 4)  # Abnormal command starvation only; normal STOP keeps sampling.
GUARD_PC = 29
GUARD_FIRED_PCS = (30, 31)


def pack_command(percent):
    """Return (command word, actual duty percent), quantized to 0.1%.

    The shortest representable nonzero pulse is three clocks (0.3 us).
    Values closer to an endpoint are rounded to that constant endpoint.
    """
    percent = float(percent)
    if percent != percent:
        raise ValueError('duty cannot be NaN')
    high = max(0, min(CYCLE_CLOCKS, int(max(0.0, min(100.0, percent)) * 10 + 0.5)))
    if high < 3:
        high = 0
    elif high > 997:
        high = 1000
    if high in (0, 1000):
        long_level = short_level = 1 if high else 0
        short = 500  # Virtual phases; every output has the same level.
    else:
        long_level = 1 if high >= 500 else 0
        short_level = 1 - long_level
        short = min(high, 1000 - high)
    long = 1000 - short
    # Short pulse is n2+3 clocks. Long edge to sample is n3+5;
    # sample to the next short edge is n1+15. Fixed overhead is 23.
    n2 = short - 3
    n3 = long // 2 - 5
    n1 = 977 - n2 - n3
    word = (long_level | (n1 << 1) | (short_level << 10) |
            (n2 << 11) | (long_level << 20) | (n3 << 21))
    return word, high / 10.0


@asm_pio(out_init=PIO.OUT_LOW, set_init=PIO.OUT_LOW,
         out_shiftdir=PIO.SHIFT_RIGHT)
def combined_program():
    wrap_target()
    label('cycle')
    mov(y, status) [3]             # 0: preserve the former three STOP clocks.
    jmp(not_y, 'command')          # 1
    irq(rel(0))                   # 2: latch command-underrun fault flag.
    label('stop')
    set(pins, 0)                  # 3: abnormal starvation stays fail-closed.
    jmp('stop')                   # 4
    label('command')
    pull(block)                   # 5: guard guarantees a word is available.
    out(pins, 1)                  # 6: long phase starts/continues here.
    out(y, 9)                     # 7
    label('first_half')
    jmp(y_dec, 'first_half')       # 8
    out(pins, 1)                  # 9: short phase.
    out(y, 9)                     # 10
    label('short_phase')
    jmp(y_dec, 'short_phase')      # 11
    out(pins, 1)                  # 12: return to the long phase.
    out(y, 9)                     # 13
    label('last_half')
    jmp(y_dec, 'last_half')        # 14
    jmp(x_dec, 'dispatch')        # 15: decrement, even when X was zero.
    label('dispatch')
    mov(pc, isr)                  # 16: previous tach state chooses 17/22.
    jmp(pin, 'rising')            # 17: previously LOW.
    jmp('cycle') [4]              # 18
    label('rising')
    set(y, 22)                    # 19
    mov(isr, y)                   # 20
    jmp('cycle') [2]              # 21
    jmp(pin, 'still_high')        # 22: previously HIGH.
    mov(isr, invert(x))           # 23: complete falling-to-falling period.
    push(noblock)                 # 24: never pause PWM for the consumer.
    mov(x, invert(null))          # 25
    set(y, 17)                    # 26
    mov(isr, y)                   # 27
    wrap()
    label('still_high')
    jmp('cycle') [4]              # 28: six clocks for every tach branch.
    # A separate SM starts here with ISR preloaded to GPIO OUTOVER_LOW bit13.
    # The first RX-paced DMA consumes that token, then chains the other pads.
    label('guard')
    wait(0, gpio, 14)             # 29: hardware STOP, independent of Python.
    push(block)                   # 30
    label('guard_latched')
    jmp('guard_latched')          # 31: only explicit START re-arms the watcher.
