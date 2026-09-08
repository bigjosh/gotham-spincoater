"""Resource/lifecycle tests; actual PIO timing is tested separately."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class PWMStub:
    def __init__(self, duty=0):
        self.duty = duty

    def freq(self):
        return 10000

    def duty_u16(self, value=None):
        if value is not None:
            self.duty = value
        return self.duty


class DMAStub:
    all = []
    events = []

    def __init__(self):
        self.channel = len(self.all)
        self.all.append(self)
        self.dead = False
        self.ctrl = {'enable': True}

    def pack_ctrl(self, default=None, **kwargs):
        result = dict(default or {'enable': True})
        result.update(kwargs)
        return result

    def config(self, **kwargs):
        self.configured = kwargs
        self.ctrl = kwargs['ctrl']

    def active(self, value):
        if value == 0:
            raise AssertionError('Must disable whole chain before aborting')
        self.events.append(('start', self.channel))

    def close(self):
        if not self.dead:
            # All remaining members of the same group must be disabled first.
            group = getattr(self, 'group', None)
            if group is not None:
                assert all(not d.ctrl['enable'] for d in group if not d.dead)
            self.events.append(('close', self.channel))
            self.dead = True


class SMStub:
    def __init__(self, sm_id):
        self.sm_id = sm_id
        self.words = []

    def init(self, program, **kwargs):
        self.kwargs = kwargs
        self.words = []

    def active(self, value):
        self.enabled = bool(value)

    def irq(self, handler=None, hard=False):
        self.handler = handler

    def rx_fifo(self):
        return len(self.words)

    def get(self, target):
        target[0] = self.words.pop(0) & 0xffff


class PIOStub:
    removed = []

    def __init__(self, block):
        self.block = block

    def remove_program(self, program):
        self.removed.append(self.block)


class MeasurementsStub:
    def __init__(self, **kwargs):
        self.periods = []

    def record(self, duration):
        self.periods.append(duration)

    def sample(self):
        return {}


class SyncTests(unittest.TestCase):
    def setUp(self):
        DMAStub.all, DMAStub.events, PIOStub.removed = [], [], []
        dependencies = {
            'machine': types.SimpleNamespace(freq=lambda: 150000000),
            'rp2': types.SimpleNamespace(DMA=DMAStub, StateMachine=SMStub, PIO=PIOStub),
            'periods': types.SimpleNamespace(PeriodMeasurements=MeasurementsStub),
            'sync_program': types.SimpleNamespace(sync_program=object(),
                                                 PIO_CLOCK_HZ=10000000,
                                                 SAMPLE_OVERHEAD_CYCLES=4),
        }
        path = Path(__file__).resolve().parents[1] / 'device' / 'sync_tach.py'
        spec = importlib.util.spec_from_file_location('sync_under_test', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, dependencies):
            spec.loader.exec_module(module)
        self.Reader = module.SyncTachometer
        self.readers = []

    def reader(self, pin=18, sm=0, duty=0):
        result = self.Reader(PWMStub(duty), pin, object(), sm_id=sm)
        self.readers.append(result)
        return result

    def tearDown(self):
        for reader in self.readers:
            reader.close()

    def test_longer_phase_midpoint_and_endpoints(self):
        reader = self.reader()
        for duty, expected in ((0, 50), (16384, 62.5), (32768, 25),
                               (49151, 37.5), (65535, 50)):
            reader.set_duty_u16(duty)
            self.assertAlmostEqual(reader.sample_offset_us, expected, places=2)
            self.assertAlmostEqual(reader._token[0] + 4, expected * 10, delta=1)
            self.assertEqual(reader._dma.configured['count'], 0xf0000001)
            self.assertEqual(reader._dma.ctrl['treq_sel'], 33)

    def test_shared_wrap_has_exactly_one_dreq_consumer(self):
        readers = [self.reader(pin=12 + i % 2, sm=sm)
                   for i, sm in enumerate((0, 1, 2, 3, 8, 9))]
        dmas = [reader._dma for reader in readers]
        self.assertEqual([d.ctrl['treq_sel'] for d in dmas], [38, 63, 63, 63, 63, 63])
        self.assertTrue(all(d.configured['count'] == 1 for d in dmas))
        for index, dma in enumerate(dmas):
            self.assertEqual(dma.ctrl['chain_to'], dmas[(index + 1) % 6].channel)
            dma.group = dmas
        readers[0].set_duty_u16(32768)  # close asserts all EN bits were cleared.

    def test_unrelated_wraps_keep_independent_dma(self):
        first = self.reader(pin=18)
        second = self.reader(pin=20, sm=1)
        saved_dma = first._dma
        second.set_duty_u16(32768)
        self.assertIs(first._dma, saved_dma)
        self.assertFalse(saved_dma.dead)
        self.assertEqual(second._dma.ctrl['treq_sel'], 34)

    def test_close_one_shared_reader_rearms_survivor(self):
        first = self.reader(pin=12)
        second = self.reader(pin=13, sm=1)
        first.close()
        self.assertEqual(second._dma.configured['count'], 0xf0000001)
        self.assertTrue(second._sm.enabled)
        self.assertEqual(PIOStub.removed, [])
        second.close()
        self.assertEqual(PIOStub.removed, [0])
        self.assertEqual(self.Reader._claimed_sms, set())
        self.assertEqual(self.Reader._pacers, {})

    def test_first_partial_period_is_discarded(self):
        reader = self.reader()
        reader._sm.words = [42, 100, 100]
        reader._on_period(reader._sm)
        self.assertEqual(reader.measurements.periods, [0, 10000, 10000])
        reader.set_duty_u16(20000)
        reader._sm.words = [9, 100]
        reader._on_period(reader._sm)
        self.assertEqual(reader.measurements.periods[-2:], [0, 10000])

    def test_unchanged_duty_does_not_reset_measurements(self):
        reader = self.reader(duty=1000)
        saved_dma = reader._dma
        reader.set_duty_u16(1000)
        self.assertIs(reader._dma, saved_dma)

    def test_duplicate_sm_rejected_before_touching_existing_reader(self):
        reader = self.reader()
        saved_dma = reader._dma
        with self.assertRaises(ValueError):
            self.reader(pin=20, sm=0)
        self.assertIs(reader._dma, saved_dma)


if __name__ == '__main__':
    unittest.main()
