"""Instruction-level checks of timing and full-period counting, without GPIO.

The small interpreter executes the actual decorated PIO function under a
recording assembler. It models the instructions used here, including JMP's
unconditional post-decrement and stalls while waiting for DMA tokens.
"""
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def load_program():
    instructions = []
    labels = {}
    markers = {}
    names = ("x", "y", "null", "osr", "isr", "block", "noblock",
             "y_dec", "x_dec", "pin")

    def assembler():
        def decorate(fn):
            namespace = fn.__globals__
            namespace.update({name: name for name in names})
            for name in ("mov", "pull", "jmp", "push", "irq"):
                namespace[name] = lambda *args, _name=name: instructions.append(
                    (_name, args))
            namespace["label"] = lambda name: labels.__setitem__(
                name, len(instructions))
            namespace["wrap_target"] = lambda: markers.__setitem__(
                "wrap_target", len(instructions))
            namespace["wrap"] = lambda: markers.__setitem__(
                "wrap", len(instructions) - 1)
            namespace["invert"] = lambda source: ("invert", source)
            namespace["rel"] = lambda value: ("rel", value)
            fn()
            return instructions
        return decorate

    fake = types.ModuleType("rp2")
    fake.asm_pio = assembler
    original = sys.modules.get("rp2")
    sys.modules["rp2"] = fake
    try:
        path = Path(__file__).resolve().parents[1] / "device" / "sync_program.py"
        spec = importlib.util.spec_from_file_location("_sync_program_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if original is None:
            del sys.modules["rp2"]
        else:
            sys.modules["rp2"] = original
    return module, instructions, labels, markers


class Interpreter:
    def __init__(self):
        self.module, self.code, self.labels, self.markers = load_program()
        self.registers = {name: 0 for name in ("x", "y", "osr", "isr", "null")}
        self.pc = 0
        self.cycle = 0
        self.samples = []
        self.words = []
        self.irqs = 0

    def run(self, tokens, tach):
        """tokens is [(arrival_clock, delay_word), ...]; tach(clock) -> level."""
        tokens = iter(tokens)
        for _ in range(10_000_000):
            operation, args = self.code[self.pc]
            next_pc = self.pc + 1
            if operation == "pull":
                try:
                    arrival, word = next(tokens)
                except StopIteration:
                    return
                self.cycle = max(self.cycle, arrival)
                self.registers["osr"] = word
            elif operation == "mov":
                destination, source = args
                if isinstance(source, tuple):
                    value = ~self.registers[source[1]]
                else:
                    value = self.registers[source]
                self.registers[destination] = value & 0xffffffff
            elif operation == "jmp":
                if len(args) == 1:
                    take, target = True, args[0]
                else:
                    condition, target = args
                    if condition in ("x_dec", "y_dec"):
                        register = condition[0]
                        before = self.registers[register]
                        self.registers[register] = (before - 1) & 0xffffffff
                        take = before != 0
                    elif condition == "pin":
                        take = bool(tach(self.cycle))
                        self.samples.append((self.cycle, take))
                    else:
                        raise AssertionError(condition)
                if take:
                    next_pc = self.labels[target]
            elif operation == "push":
                self.words.append((self.cycle, self.registers["isr"]))
                self.registers["isr"] = 0
            elif operation == "irq":
                self.irqs += 1
            else:
                raise AssertionError(operation)
            if self.pc == self.markers["wrap"] and next_pc == self.pc + 1:
                next_pc = self.markers["wrap_target"]
            self.pc = next_pc
            self.cycle += 1
        raise AssertionError("instruction budget exceeded")


class SyncProgramTests(unittest.TestCase):
    def test_program_fits_one_shared_instruction_memory(self):
        machine = Interpreter()
        self.assertLessEqual(len(machine.code), 32)
        self.assertEqual(machine.module.PIO_CLOCK_HZ, 10_000_000)

    def test_sample_offset_identical_for_both_states_and_endpoints(self):
        for delay in (0, 246, 496, 745):
            with self.subTest(delay=delay):
                machine = Interpreter()
                arrivals = [1000 + index * 1000 for index in range(50)]
                machine.run([(clock, delay) for clock in arrivals],
                            lambda clock: (clock // 7000) % 2)
                self.assertEqual([sample[0] - start for sample, start
                                  in zip(machine.samples, arrivals)],
                                 [delay + machine.module.SAMPLE_OVERHEAD_CYCLES]
                                 * len(arrivals))

    def test_full_periods_have_no_off_by_one(self):
        for period in (2, 3, 75, 100, 113, 300):
            with self.subTest(period=period):
                machine = Interpreter()
                machine.run([(1000 + i * 1000, 496)
                             for i in range(period * 5)],
                            lambda clock: ((clock - 1000) // 1000) % period
                            < max(1, period // 2))
                self.assertGreaterEqual(len(machine.words), 4)
                self.assertEqual([word for _, word in machine.words[1:]],
                                 [period] * (len(machine.words) - 1))
                self.assertEqual(machine.irqs, len(machine.words))

    def test_constant_tach_does_not_emit_stale_repeated_values(self):
        for level in (False, True):
            machine = Interpreter()
            machine.run([(1000 + i * 1000, 496) for i in range(100)],
                        lambda clock: level)
            self.assertEqual(machine.words, [])

    def test_edge_glitches_away_from_sampling_point_are_not_counted(self):
        machine = Interpreter()
        # Ten-microsecond spikes at each PWM wrap are gone by the sample at
        # 50 us; a 100-PWM-cycle tach waveform remains 3000 RPM at two PPR.
        def tach(clock):
            logical = ((clock - 1000) // 1000) % 100 < 50
            return not logical if clock % 1000 < 100 else logical
        machine.run([(1000 + i * 1000, 496) for i in range(500)], tach)
        self.assertEqual([word for _, word in machine.words[1:]], [100] * 4)

    def test_counter_wrap_still_counts_complete_periods(self):
        machine = Interpreter()
        # Execute initialization, then start just before the 32-bit wrap.
        machine.pc = machine.labels["low"]
        machine.registers["x"] = 1
        machine.run([(1000 + i * 1000, 496) for i in range(12)],
                    lambda clock: ((clock - 1000) // 1000) % 4 < 2)
        self.assertEqual([word for _, word in machine.words[1:]], [4, 4])


if __name__ == "__main__":
    unittest.main()
