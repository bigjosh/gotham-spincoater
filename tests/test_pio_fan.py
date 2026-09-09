"""Driver ownership, STOP rearm, DMA pacing and stale-data checks."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from test_combined_program import load_program

ROOT = Path(__file__).resolve().parents[1]


class Memory:
    def __init__(self):
        self.data = {}

    def __getitem__(self, address):
        return self.data.get(address, 0)

    def __setitem__(self, address, value):
        if address & 0xfffff in (8, 0x30):
            self.data[address] = self.data.get(address, 0) & ~value
        else:
            self.data[address] = value


class PinStub:
    OUT, IN, PULL_UP = 1, 0, 2
    levels = {}

    def __init__(self, number, mode=None, pull=None, value=None):
        self.number = number
        self.levels.setdefault(number, 1)
        self.init(mode, value=value)

    def init(self, mode, value=None):
        self.mode = mode
        if value is not None:
            self.levels[self.number] = value

    def value(self):
        return self.levels[self.number]


class DMAStub:
    all = []

    def __init__(self):
        self.channel = len(self.all)
        self.all.append(self)
        self.dead = False

    def pack_ctrl(self, **kwargs):
        return kwargs

    def config(self, **kwargs):
        self.configured = kwargs

    def close(self):
        self.dead = True


class SMStub:
    all = []
    memory = None

    def __init__(self, number):
        self.number = number
        self.rx, self.tx = [], []
        self.inits = 0
        self.initialized = False
        self.execs = []
        self.fail_exec = False
        self.addr = 0x50200000 + number // 4 * 0x100000 + 0xd4 + number % 4 * 0x18
        self.all.append(self)

    def active(self, value):
        self.enabled = value

    def init(self, program, **kwargs):
        self.inits += 1
        self.initialized = True
        self.options = kwargs
        self.rx, self.tx = [], []
        self.memory.data[self.addr] = 0

    def exec(self, instruction):
        if not self.initialized:
            raise AssertionError('Never execute pin instructions before owning its pin mapping')
        if self.fail_exec:
            raise RuntimeError('Injected instruction failed')
        assert type(instruction) is int, 'Use encoded exec instructions'
        self.execs.append(instruction)
        if instruction >> 13 == 0:
            self.memory.data[self.addr] = instruction & 31

    def put(self, word):
        assert len(self.tx) < 4
        self.tx.append(word)

    def rx_fifo(self):
        return len(self.rx)

    def get(self, target):
        target[0] = self.rx.pop(0)


class PIOStub:
    removed = []

    def __init__(self, block):
        self.block = block

    def remove_program(self, program):
        self.removed.append(self.block)


class MeasurementsStub:
    def __init__(self, **kwargs):
        self.records = []
        self.values = []

    def record(self, value):
        self.records.append(value)
        if value:
            self.values.append(value)
        else:
            self.values = []

    def sample(self):
        return {'valid': len(self.values) >= 2, 'rpm': 3000 if self.values else 0}


class PioFanTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory()
        self.clock = 0
        self.frequency = 150000000
        self.fans = []
        PinStub.levels, DMAStub.all, SMStub.all, PIOStub.removed = {}, [], [], []
        SMStub.memory = self.memory
        program_module, words, _, _ = load_program()
        program_module.combined_program = [words]
        deps = {
            'machine': types.SimpleNamespace(Pin=PinStub, freq=lambda: self.frequency,
                                            mem32=self.memory),
            'rp2': types.SimpleNamespace(DMA=DMAStub, PIO=PIOStub, StateMachine=SMStub),
            'time': types.SimpleNamespace(ticks_us=lambda: self.clock,
                                         ticks_diff=lambda a, b: a - b),
            'periods': types.SimpleNamespace(PeriodMeasurements=MeasurementsStub),
            'combined_program': program_module,
        }
        spec = importlib.util.spec_from_file_location('_pio_fan_test', ROOT / 'device/pio_fan.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, deps):
            spec.loader.exec_module(module)
        self.Fan = module.PioFan

    def fan(self, pwm=18, tach=19, sm=0):
        fan = self.Fan(pwm, tach, sm)
        self.fans.append(fan)
        return fan

    def tearDown(self):
        for fan in self.fans:
            fan.close()

    def test_six_channels_have_unique_tx_dreq_and_share_program(self):
        pins = ((18, 19), (20, 21), (22, 28), (1, 0), (13, 12), (16, 17))
        fans = [self.fan(*pair, sm) for pair, sm in zip(pins, (0, 1, 2, 3, 8, 9))]
        self.assertEqual([f._dma.configured['ctrl']['treq_sel'] for f in fans],
                         [0, 1, 2, 3, 16, 17])
        self.assertTrue(all(f._dma.configured['count'] == 0xf0000001 for f in fans))
        self.assertTrue(all(f._sm.tx == [f._command[0]] * 4 for f in fans))
        fans[0].close()
        self.assertEqual(PIOStub.removed, [])
        for fan in fans[1:]:
            fan.close()
        self.assertEqual(PIOStub.removed, [0, 2])
        self.assertEqual(self.Fan._claimed_sms, set())

    def test_duty_updates_do_not_reset_any_state_machine_or_measurement(self):
        fan = self.fan()
        fan._sm.rx = [30, 100, 100]
        self.assertTrue(fan.sample()['valid'])
        dma, inits, records = fan._dma, fan._sm.inits, len(fan.measurements.records)
        for duty in (25, 50, 75, 0, 100):
            self.assertEqual(fan.set_duty(duty), duty)
        self.assertIs(fan._dma, dma)
        self.assertEqual(fan._sm.inits, inits)
        self.assertEqual(len(fan.measurements.records), records)
        self.assertTrue(fan.sample()['valid'])

    def test_stop_latches_and_arm_clears_commands_only_after_button_release(self):
        fan = self.fan()
        fan.set_duty(80)
        dma = fan._dma
        self.memory.data[fan._addr] = 6  # PIO caught a brief button press.
        self.assertEqual(fan.set_duty(90), 0)
        self.assertTrue(dma.dead)
        self.assertEqual(PinStub.levels[18], 0)
        self.assertTrue(fan.sample()['stopped'])
        PinStub.levels[14] = 0
        self.assertFalse(fan.arm())
        PinStub.levels[14] = 1
        self.assertTrue(fan.arm())
        self.assertEqual(fan._duty, 0)
        self.assertEqual(fan._sm.tx, [fan._command[0]] * 4)
        self.assertFalse(fan.sample()['stopped'])

    def test_held_stop_at_boot_does_not_execute_unconfigured_pio_pin_writes(self):
        PinStub.levels[14] = 0
        fan = self.fan()
        self.assertTrue(fan.sample()['stopped'])
        self.assertEqual(fan._sm.inits, 0)
        self.assertEqual(PinStub.levels[18], 0)

    def test_hardware_command_underrun_flag_stops_and_is_cleared_by_arm(self):
        fan = self.fan()
        fan.set_duty(50)
        self.memory.data[fan._irq] = fan._bit
        reading = fan.sample()
        self.assertEqual(reading['fault'], 'command_underrun')
        self.assertTrue(reading['stopped'])
        self.assertEqual(reading['duty'], 0)
        self.assertTrue(fan.arm())
        self.assertIsNone(fan.sample()['fault'])

    def test_rx_overflow_discards_queue_and_requires_new_baseline(self):
        fan = self.fan()
        fan._sm.rx = [30, 100, 100]
        self.assertTrue(fan.sample()['valid'])
        self.memory.data[fan._debug] = fan._bit
        fan._sm.rx = [100] * 4
        reading = fan.sample()
        self.assertTrue(reading['overflow'])
        self.assertFalse(reading['valid'])
        self.assertEqual(reading['overflows'], 1)
        self.assertEqual(fan._sm.rx, [])
        fan._sm.rx = [100, 100]
        self.assertFalse(fan.sample()['valid'])
        fan._sm.rx = [100]
        self.assertTrue(fan.sample()['valid'])

    def test_long_poll_gap_and_long_period_do_not_alias_into_valid_rpm(self):
        fan = self.fan()
        fan._sm.rx = [30, 100, 100]
        self.assertTrue(fan.sample()['valid'])
        self.clock += 1600000
        fan._sm.rx = [100] * 4
        self.assertFalse(fan.sample()['valid'])
        fan._sm.rx = [100, 0x10064, 100]
        self.assertFalse(fan.sample()['valid'])

    def test_rejected_duplicate_never_touches_existing_output(self):
        fan = self.fan()
        fan.set_duty(50)
        with self.assertRaises(ValueError):
            self.fan(20, 21, 0)
        with self.assertRaises(ValueError):
            self.fan(18, 21, 1)
        self.assertFalse(fan._dma.dead)
        self.assertEqual(fan._duty, 50)

    def test_runtime_clock_change_fails_closed(self):
        fan = self.fan()
        fan.set_duty(50)
        self.frequency = 125000000
        self.assertEqual(fan.sample()['fault'], 'clock_changed')
        self.assertTrue(fan._stopped)
        self.assertFalse(fan.arm())

    def test_stop_closes_dma_even_when_injected_instruction_raises(self):
        fan = self.fan()
        dma = fan._dma
        fan.set_duty(75)
        fan._sm.fail_exec = True
        with self.assertRaises(RuntimeError):
            fan.stop()
        self.assertTrue(dma.dead)
        self.assertIsNone(fan._dma)
        self.assertEqual(PinStub.levels[18], 0)
        self.assertEqual(fan._sm.enabled, 0)
        self.assertTrue(fan._stopped)
        fan._sm.fail_exec = False

    def test_injected_words_match_micropython_encoder_and_jmp_is_numeric(self):
        path = ROOT / 'artifacts/micropython-rp2-v1.29.0.py'
        if not path.exists():
            self.skipTest('Optional downloaded official assembler is absent')
        from test_combined_program import PIOStub as AssemblerPIO
        spec = importlib.util.spec_from_file_location('_official_exec_assembler', path)
        official = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
                '_rp2': types.SimpleNamespace(PIO=AssemblerPIO),
                'micropython': types.SimpleNamespace(const=lambda x: x)}):
            spec.loader.exec_module(official)
        fan = self.fan()
        self.assertEqual(fan._sm.execs[:3], [
            official.asm_pio_encode('mov(x, invert(null))', 0),
            official.asm_pio_encode('set(y, 20)', 0),
            official.asm_pio_encode('mov(isr, y)', 0)])
        # The real encoder rejects JMP strings. The hardware accepts its
        # numeric opcode directly, so never pass this expression to exec().
        with self.assertRaises(TypeError):
            official.asm_pio_encode('jmp(6)', 0)
        fan.stop()
        self.assertEqual(fan._sm.execs[-2:], [
            official.asm_pio_encode('set(pins, 0)', 0), 6])


if __name__ == '__main__':
    unittest.main()
