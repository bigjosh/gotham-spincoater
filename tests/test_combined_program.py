"""Assemble/decode the combined PIO instructions and verify their timing.

The small assembler implements only used RP2350 opcodes. When the downloaded
official MicroPython assembler is available, an additional test compares the
actual 16-bit words against it. The timing interpreter consumes encoded words,
not a parallel handwritten model of the fan algorithm.
"""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PIOStub:
    OUT_LOW = 0
    SHIFT_LEFT = 0
    SHIFT_RIGHT = 1
    JOIN_NONE = 0


class Instruction:
    def __init__(self, op, args):
        self.op, self.args, self.delay = op, args, 0

    def __getitem__(self, delay):
        self.delay = delay
        return self


def load_program():
    instructions, labels, markers = [], {}, {}

    def asm_pio(**options):
        def decorate(fn):
            g = fn.__globals__
            for name in ('x', 'y', 'pins', 'pc', 'isr', 'osr', 'null', 'status',
                         'not_y', 'x_dec', 'y_dec', 'pin', 'gpio', 'block', 'noblock'):
                g[name] = name
            def emit(op, *args):
                item = Instruction(op, args)
                instructions.append(item)
                return item
            for op in ('mov', 'out', 'jmp', 'set', 'pull', 'push', 'irq', 'wait'):
                g[op] = lambda *a, _op=op: emit(_op, *a)
            g['label'] = lambda label: labels.__setitem__(label, len(instructions))
            g['wrap_target'] = lambda: markers.__setitem__('bottom', len(instructions))
            g['wrap'] = lambda: markers.__setitem__('top', len(instructions) - 1)
            g['invert'] = lambda x: ('invert', x)
            g['rel'] = lambda x: ('rel', x)
            fn()
            return instructions
        return decorate

    spec = importlib.util.spec_from_file_location('combined_under_test', ROOT / 'device/combined_program.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'rp2': types.SimpleNamespace(PIO=PIOStub, asm_pio=asm_pio)}):
        spec.loader.exec_module(module)
    words = []
    mov_dest = {'pins': 0, 'x': 1, 'y': 2, 'pc': 5, 'isr': 6, 'osr': 7}
    mov_src = {'pins': 0, 'x': 1, 'y': 2, 'null': 3, 'status': 5, 'isr': 6, 'osr': 7}
    for item in instructions:
        op, args = item.op, item.args
        if op == 'jmp':
            condition, target = (0, args[0]) if len(args) == 1 else (
                {'not_y': 3, 'x_dec': 2, 'y_dec': 4, 'pin': 6}[args[0]], args[1])
            value = (condition << 5) | labels.get(target, target)
        elif op == 'out':
            value = 0x6000 | ({'pins': 0, 'y': 2}[args[0]] << 5) | args[1]
        elif op == 'mov':
            source = args[1]
            invert = isinstance(source, tuple)
            if invert:
                source = source[1]
            value = 0xa000 | (mov_dest[args[0]] << 5) | (int(invert) << 3) | mov_src[source]
        elif op == 'set':
            value = 0xe000 | ({'pins': 0, 'y': 2}[args[0]] << 5) | args[1]
        elif op == 'pull':
            value = 0x80a0
        elif op == 'push':
            value = 0x8020 if args[0] == 'block' else 0x8000
        elif op == 'wait':
            assert args[1] == 'gpio'
            value = 0x2000 | (args[0] << 7) | args[2]
        elif op == 'irq':
            value = 0xc010 | args[0][1]
        else:
            raise AssertionError(op)
        words.append(value | (item.delay << 8))
    return module, words, labels, markers


class Interpreter:
    def __init__(self, command, tach=lambda c: False, stop=lambda c: False):
        self.module, self.code, self.labels, self.markers = load_program()
        self.pc = 0
        self.cycle = 0
        self.reg = {'x': 0xffffffff, 'y': self.module.LOW_SAMPLE_PC,
                    'osr': 0, 'isr': self.module.LOW_SAMPLE_PC}
        self.output = 0
        self.outputs = []
        self.samples = []
        self.periods = []
        self.command = command if callable(command) else lambda frame: command
        self.tach, self.stop_input = tach, stop
        self.frame = 0
        self.pending = None
        self.fault = False
        self.stop_at = None

    def run(self, until):
        while self.cycle < until:
            word = self.code[self.pc]
            op, delay = word >> 13, (word >> 8) & 31
            next_pc = self.pc + 1
            if op == 0:
                condition, target = (word >> 5) & 7, word & 31
                if condition == 0:
                    take = True
                elif condition == 3:
                    take = not self.reg['y']
                elif condition in (2, 4):
                    reg = 'x' if condition == 2 else 'y'
                    take = bool(self.reg[reg])
                    self.reg[reg] = (self.reg[reg] - 1) & 0xffffffff
                elif condition == 6:
                    take = bool(self.tach(self.cycle))
                    self.samples.append((self.cycle, take))
                else:
                    raise AssertionError(condition)
                if take:
                    next_pc = target
            elif op == 1:
                self.assert_wait_gpio(word)
                level = 0 if self.stop_input(self.cycle) else 1
                if level != ((word >> 7) & 1):
                    next_pc = self.pc
            elif op == 3:
                destination, count = (word >> 5) & 7, word & 31
                value = self.reg['osr'] & ((1 << count) - 1)
                self.reg['osr'] >>= count
                if destination == 2:
                    self.reg['y'] = value
                elif destination == 0:
                    self.output = value
                    self.outputs.append((self.cycle, value, self.pc))
                else:
                    raise AssertionError(destination)
            elif op == 4:
                if word & 0x80:
                    if self.pending is None:
                        raise AssertionError('A PULL must never block')
                    self.reg['osr'] = self.pending
                    self.pending = None
                    self.frame += 1
                else:
                    self.periods.append((self.cycle, self.reg['isr']))
                    self.reg['isr'] = 0
            elif op == 5:
                destination, source = (word >> 5) & 7, word & 7
                if source == 0:
                    value = 0 if self.stop_input(self.cycle) else 1
                elif source == 3:
                    value = 0
                elif source == 5:
                    self.pending = self.command(self.frame)
                    value = 0xffffffff if self.pending is None else 0
                else:
                    value = self.reg[{1: 'x', 2: 'y', 6: 'isr', 7: 'osr'}[source]]
                if word & 8:
                    value = ~value & 0xffffffff
                if destination == 5:
                    next_pc = value & 31
                else:
                    self.reg[{1: 'x', 2: 'y', 6: 'isr', 7: 'osr'}[destination]] = value
            elif op == 6:
                self.fault = True
            elif op == 7:
                destination, value = (word >> 5) & 7, word & 31
                if destination == 0:
                    self.output = value
                    self.outputs.append((self.cycle, value, self.pc))
                    if self.stop_at is None:
                        self.stop_at = self.cycle
                else:
                    self.reg['y'] = value
            else:
                raise AssertionError(op)
            if self.pc == self.markers['top'] and next_pc == self.pc + 1:
                next_pc = self.markers['bottom']
            self.pc = next_pc
            self.cycle += 1 + delay
        return self

    @staticmethod
    def assert_wait_gpio(word):
        assert (word >> 5) & 3 == 0
        assert word & 31 == 14


class CombinedProgramTests(unittest.TestCase):
    def test_exactly_32_words_and_computed_jump_addresses(self):
        module, words, labels, markers = load_program()
        self.assertEqual(len(words), 32)
        self.assertEqual(markers, {'bottom': 0, 'top': 27})
        self.assertEqual(labels['stop'], module.STOP_PC)
        self.assertEqual(labels['guard'], module.GUARD_PC)
        self.assertEqual(words[module.LOW_SAMPLE_PC] >> 5 & 7, 6)
        self.assertEqual(words[module.HIGH_SAMPLE_PC] >> 5 & 7, 6)

    def test_official_micropython_assembler_agrees_when_available(self):
        path = ROOT / 'artifacts/micropython-rp2-v1.29.0.py'
        if not path.exists():
            self.skipTest('Optional downloaded official assembler is absent')
        spec = importlib.util.spec_from_file_location('_official_rp2_assembler', path)
        official = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
                '_rp2': types.SimpleNamespace(PIO=PIOStub),
                'micropython': types.SimpleNamespace(const=lambda x: x)}):
            spec.loader.exec_module(official)
        spec = importlib.util.spec_from_file_location('_official_combined', ROOT / 'device/combined_program.py')
        actual = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'rp2': official}):
            spec.loader.exec_module(actual)
        self.assertEqual(list(actual.combined_program[0]), load_program()[1])

    def test_exact_cycle_length_for_every_tach_state_and_transition(self):
        module = load_program()[0]
        for duty in (0, 0.3, 1, 25, 49.9, 50, 50.1, 75, 99.7, 100):
            with self.subTest(duty=duty):
                machine = Interpreter(module.pack_command(duty)[0],
                                      tach=lambda c: c // 3000 % 2).run(25000)
                self.assertEqual([b[0] - a[0] for a, b in zip(machine.samples, machine.samples[1:])],
                                 [1000] * (len(machine.samples) - 1))

    def test_midpoint_is_equally_far_from_the_two_long_phase_edges(self):
        module = load_program()[0]
        for duty in (0.3, 1, 25, 49.9, 50, 50.1, 75, 99.7):
            with self.subTest(duty=duty):
                machine = Interpreter(module.pack_command(duty)[0]).run(6000)
                for sample, _ in machine.samples[1:-1]:
                    before = max(c for c, value, pc in machine.outputs if pc == 12 and c < sample)
                    after = min(c for c, value, pc in machine.outputs if pc == 9 and c > sample)
                    self.assertLessEqual(abs((sample - before) - (after - sample)), 1)
                    self.assertGreaterEqual(min(sample - before, after - sample), 250)

    def test_pwm_duty_and_constant_endpoints(self):
        module = load_program()[0]
        for duty in (0, 0.3, 5, 25, 49.9, 50, 75, 99.7, 100):
            with self.subTest(duty=duty):
                machine = Interpreter(module.pack_command(duty)[0]).run(4000)
                if duty in (0, 100):
                    self.assertEqual(set(v for _, v, _ in machine.outputs), {int(duty == 100)})
                else:
                    short_starts = [c for c, _, pc in machine.outputs if pc == 9]
                    short_ends = [c for c, _, pc in machine.outputs if pc == 12]
                    self.assertEqual([e - s for s, e in zip(short_starts, short_ends)],
                                     [round(min(duty, 100 - duty) * 10)] * len(short_starts))

    def test_continuous_duty_updates_do_not_reset_period_counter(self):
        module = load_program()[0]
        duties = (0, 25, 49.9, 50, 75, 100, 50.1, 1)
        machine = Interpreter(lambda frame: module.pack_command(duties[frame % len(duties)])[0],
                              tach=lambda c: c // 50000 % 2).run(510000)
        self.assertEqual([p for _, p in machine.periods[1:]], [100] * (len(machine.periods) - 1))
        self.assertGreaterEqual(len(machine.periods), 5)

    def test_switching_edge_spikes_do_not_change_reported_periods(self):
        module = load_program()[0]
        for duty in (25, 50, 75):
            machine = Interpreter(module.pack_command(duty)[0])
            def tach(clock):
                logical = clock // 50000 % 2
                # Inject 2 us glitches after either physical PWM transition.
                edge = max((c for c, _, pc in machine.outputs if pc in (9, 12)), default=-100)
                return 1 - logical if clock - edge < 20 else logical
            machine.tach = tach
            machine.run(510000)
            self.assertEqual([p for _, p in machine.periods[1:]],
                             [100] * (len(machine.periods) - 1))

    def test_shared_stop_watcher_latches_one_dma_token_after_one_clock_press(self):
        module = load_program()[0]
        for press in range(0, 2000, 61):
            machine = Interpreter(None, stop=lambda c, p=press: p <= c < p + 1)
            machine.pc = module.GUARD_PC
            machine.reg['isr'] = 1 << 13
            machine.run(4500)
            self.assertEqual(machine.periods, [(press + 1, 1 << 13)])
            self.assertIn(machine.pc, module.GUARD_FIRED_PCS)
            self.assertFalse(machine.outputs)
            self.assertFalse(machine.fault)

    def test_stop_input_cannot_interrupt_continuous_tach_sampler(self):
        module = load_program()[0]
        for duty in (0, 25, 50, 75, 100):
            machine = Interpreter(module.pack_command(duty)[0],
                                  tach=lambda c: c // 50000 % 2,
                                  stop=lambda c: 100000 <= c < 200000).run(510000)
            self.assertEqual([p for _, p in machine.periods[1:]], [100] * 4)
            self.assertIsNone(machine.stop_at)

    def test_dma_empty_fifo_faults_low_without_reaching_blocking_pull(self):
        module = load_program()[0]
        for duty in (0, 50, 100):
            word = module.pack_command(duty)[0]
            machine = Interpreter(lambda frame: word if frame < 2 else None).run(5000)
            self.assertTrue(machine.fault)
            self.assertEqual(machine.output, 0)
            self.assertEqual(machine.frame, 2)
            self.assertIn(machine.pc, module.STOP_PCS)

    def test_out_of_range_commands_and_nan(self):
        module = load_program()[0]
        self.assertEqual(module.pack_command(-1)[1], 0)
        self.assertEqual(module.pack_command(101)[1], 100)
        self.assertEqual(module.pack_command(0.1)[1], 0)
        self.assertEqual(module.pack_command(99.9)[1], 100)
        with self.assertRaises(ValueError):
            module.pack_command(float('nan'))


if __name__ == '__main__':
    unittest.main()
