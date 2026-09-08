"""Sample tach once per PWM-wrap token; report full periods to Python.

The DMA token is a delay-loop count, not a pin level. Both remembered input
states have identical timing from token consumption to the tach sample.
One copy of this program is shared by the state machines in each PIO block;
each machine selects its own tach pin with ``jmp_pin``.
"""
from rp2 import asm_pio


PIO_CLOCK_HZ = 10_000_000
# From the start of a successful PULL to the JMP PIN sampling instant:
# PULL (1), MOV (1), delay loop (token + 1), and decrement (1).
# DMA delivery and the GPIO input synchronizer add small hardware latencies.
SAMPLE_OVERHEAD_CYCLES = 4


@asm_pio()
def sync_program():
    mov(x, invert(null))
    wrap_target()
    label("low")
    pull(block)
    mov(y, osr)
    label("delay_low")
    jmp(y_dec, "delay_low")
    # JMP decrements even at zero; its destination is the next instruction.
    jmp(x_dec, "sample_low")
    label("sample_low")
    jmp(pin, "high")
    jmp("low")

    label("high")
    pull(block)
    mov(y, osr)
    label("delay_high")
    jmp(y_dec, "delay_high")
    jmp(x_dec, "sample_high")
    label("sample_high")
    jmp(pin, "high")
    # X starts at ~0 and decreases once per sample. Therefore ~X is exactly
    # the number of PWM periods since the previous reported falling edge.
    mov(isr, invert(x))
    push(noblock)
    irq(rel(0))
    mov(x, invert(null))
    jmp("low")
    wrap()
