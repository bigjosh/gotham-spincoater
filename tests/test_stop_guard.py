"""Model the STOP watcher's RX-to-GPIO DMA chain, not physical fan motion."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
GPIO_BASE = 0x40028000
OUT_LOW = 1 << 13
EVENTS = []


class Memory:
    def __init__(self):
        self.data = {}

    def __getitem__(self, address):
        return self.data.get(address, 0)

    def __setitem__(self, address, value):
        if GPIO_BASE + 0x2000 <= address < GPIO_BASE + 0x2000 + 30 * 8:
            target = address - 0x2000
            self.data[target] = self.data.get(target, 0) | value
            EVENTS.append(('gpio-set', target, value))
        else:
            self.data[address] = value


class Pin:
    IN, PULL_UP = 0, 2
    levels = {}

    def __init__(self, gpio, *args, **kwargs):
        self.gpio = gpio
        self.levels.setdefault(gpio, 1)

    def value(self):
        return self.levels[self.gpio]


class SM:
    all = []
    memory = None

    def __init__(self, number):
        self.number = number
        self.addr = 0x50200000 + number // 4 * 0x100000 + 0xd4 + number % 4 * 0x18
        self.enabled = False
        self.rx, self.tx = [], []
        self.isr = self.osr = 0
        self.inits = []
        self.execs = []
        self.all.append(self)

    def active(self, value=None):
        if value is not None:
            self.enabled = bool(value)
            EVENTS.append(('sm-active', self.number, self.enabled))
        return self.enabled

    def init(self, program, **kwargs):
        self.inits.append(kwargs)
        self.rx, self.tx = [], []
        self.isr = self.osr = 0
        self.memory[self.addr] = 0

    def restart(self):
        self.rx, self.tx = [], []
        self.isr = self.osr = 0

    def put(self, value):
        self.tx.append(value)

    def get(self, target=None):
        value = self.rx.pop(0)
        if target is not None:
            target[0] = value
        return value

    def rx_fifo(self):
        return len(self.rx)

    def exec(self, instruction):
        self.execs.append(instruction)
        if instruction == 0x80a0:
            self.osr = self.tx.pop(0)
        elif instruction == 0xa0c7:
            self.isr = self.osr
        elif instruction >> 13 == 0:
            self.memory[self.addr] = instruction & 31
        else:
            raise AssertionError('Unexpected guard injection: %s' % hex(instruction))

    def press_and_release(self):
        """WAIT sees LOW; PUSH clears ISR; the watcher then latches at JMP."""
        assert self.enabled and self.memory[self.addr] == 29
        Pin.levels[14] = 0
        self.memory[self.addr] = 30
        self.rx.append(self.isr)
        self.isr = 0
        self.memory[self.addr] = 31
        Pin.levels[14] = 1


class DMA:
    all = []
    memory = None
    fail_at = None
    fail_config_at = None
    # Noncontiguous IDs catch code which assumes array index == DMA channel.
    ids = (10, 12, 14, 6, 8, 4, 11, 13, 15, 7, 9, 5)

    def __init__(self):
        if len(self.all) == self.fail_at:
            raise RuntimeError('DMA allocation failed')
        self.channel = self.ids[len(self.all) % len(self.ids)]
        self.enabled = False
        self.triggered = False
        self.closed = False
        self.count = 0
        self.options = {}
        self.all.append(self)

    def pack_ctrl(self, **kwargs):
        return dict(enable=True, **kwargs) if 'enable' not in kwargs else dict(kwargs)

    def config(self, **kwargs):
        if self.all.index(self) == self.fail_config_at:
            raise RuntimeError('DMA configuration failed')
        self.options.update(kwargs)
        self.count = self.options['count']
        self.enabled = self.options['ctrl'].get('enable', True)
        self.triggered = bool(kwargs.get('trigger', False))
        EVENTS.append(('dma-config', self.channel, self.triggered))

    def active(self, value=None):
        if value is not None:
            self.enabled = bool(value)
            if value:
                self.triggered = True
            EVENTS.append(('dma-enable', self.channel, self.enabled))
        return self.enabled and self.triggered and bool(self.count)

    def close(self):
        # A predecessor must not be able to restart a follower during abort.
        assert all(not d.enabled for d in self.all if not d.closed)
        EVENTS.append(('dma-close', self.channel))
        self.closed = True

    @classmethod
    def run_ready_chain(cls, limit=None):
        writes = []
        while True:
            progressed = False
            for dma in cls.all:
                if dma.closed or not dma.enabled or not dma.triggered or not dma.count:
                    continue
                source = dma.options['read']
                treq = dma.options['ctrl']['treq_sel']
                if isinstance(source, SM):
                    assert treq == 23
                    if not source.rx:
                        continue
                    word = source.rx.pop(0)
                else:
                    assert treq == 63
                    word = source[0]
                assert dma.count == 1
                cls.memory[dma.options['write']] = word
                writes.append((dma.channel, dma.options['write'], word))
                dma.count = 0
                next_channel = dma.options['ctrl']['chain_to']
                if next_channel != dma.channel:
                    next_dma = next(d for d in cls.all if not d.closed and d.channel == next_channel)
                    next_dma.triggered = True
                if limit is not None and len(writes) >= limit:
                    return writes
                progressed = True
            if not progressed:
                return writes


class PIO:
    removed = []

    def __init__(self, block):
        self.block = block

    def remove_program(self, program):
        self.removed.append(self.block)


class Fan:
    _claimed_sms = set()
    _loaded_blocks = set()
    memory = None

    def __init__(self, gpio, sm_id):
        self._sm_id = sm_id
        self._gpio_ctrl = GPIO_BASE + gpio * 8 + 4
        self.stops = 0
        self.guard = None
        self.capture_generation = 1
        self._closed = False
        self._claimed_sms.add(sm_id)
        self._loaded_blocks.add(sm_id // 4)
        self.memory[self._gpio_ctrl] = 6 + sm_id // 4

    def stop(self):
        self.stops += 1
        EVENTS.append(('fan-stop', self._sm_id))
        self.memory[self._gpio_ctrl + 0x2000] = OUT_LOW


class StopGuardTests(unittest.TestCase):
    def setUp(self):
        EVENTS.clear()
        self.memory = Memory()
        SM.memory = DMA.memory = Fan.memory = self.memory
        SM.all, DMA.all, PIO.removed = [], [], []
        Pin.levels = {}
        DMA.fail_at = DMA.fail_config_at = None
        Fan._claimed_sms, Fan._loaded_blocks = set(), set()
        self.fans = [Fan(gpio, sm) for gpio, sm in zip((18, 20, 22, 1, 13, 16), (0, 1, 2, 3, 8, 9))]
        program = [list(range(32))]
        symbols = types.SimpleNamespace(combined_program=program, PIO_CLOCK_HZ=10000000,
                                        GUARD_PC=29, GUARD_FIRED_PCS=(30, 31))
        self.modules = patch.dict(sys.modules, {
            'machine': types.SimpleNamespace(Pin=Pin, mem32=self.memory),
            'rp2': types.SimpleNamespace(DMA=DMA, PIO=PIO, StateMachine=SM),
            'pio_fan': types.SimpleNamespace(PioFan=Fan), 'combined_program': symbols})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('stop_guard_under_test', ROOT / 'device/stop_guard.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.guard = None

    def tearDown(self):
        try:
            if self.guard is not None:
                self.guard.close()
        finally:
            self.modules.stop()

    def make_guard(self):
        self.guard = self.module.StopGuard(self.fans)
        return self.guard

    def test_stop_pulse_drains_rx_and_forces_all_six_outputs_low_once(self):
        guard = self.make_guard()
        self.assertTrue(guard.fired())  # Unarmed is fail-closed.
        self.assertTrue(guard.arm())
        self.assertFalse(guard.fired())
        watcher = SM.all[-1]
        self.assertEqual(watcher.isr, OUT_LOW)
        self.assertEqual(len(DMA.all), 6)
        self.assertEqual(sum(bool(d.options.get('trigger')) for d in DMA.all), 1)
        self.assertEqual(DMA.run_ready_chain(), [])
        for fan in self.fans:
            self.memory.data[fan._gpio_ctrl] &= ~OUT_LOW  # Model explicit fan arm.
        watcher.press_and_release()
        writes = DMA.run_ready_chain()
        self.assertEqual([address for _, address, _ in writes],
                         [fan._gpio_ctrl + 0x2000 for fan in self.fans])
        self.assertEqual([word for _, _, word in writes], [OUT_LOW] * 6)
        self.assertEqual(watcher.rx, [])
        self.assertEqual(Pin.levels[14], 1)
        self.assertTrue(guard.fired())  # Detect even after release and FIFO drain.
        self.assertTrue(all(self.memory[f._gpio_ctrl] & (3 << 12) == OUT_LOW for f in self.fans))
        self.assertEqual(DMA.run_ready_chain(), [])
        self.assertEqual([fan.capture_generation for fan in self.fans], [1] * 6)

    def test_followers_are_ready_before_first_trigger_and_last_chains_to_itself(self):
        guard = self.make_guard()
        guard.arm()
        first = DMA.all[0]
        trigger_at = next(i for i, event in enumerate(EVENTS)
                          if event == ('dma-config', first.channel, True))
        for dma in DMA.all[1:]:
            self.assertLess(EVENTS.index(('dma-config', dma.channel, False)), trigger_at)
        self.assertEqual([d.options['ctrl']['chain_to'] for d in DMA.all],
                         [d.channel for d in DMA.all[1:]] + [DMA.all[-1].channel])
        for dma in DMA.all:
            self.assertEqual(dma.options['count'], 1)
            self.assertEqual(dma.options['ctrl']['size'], 2)
            self.assertFalse(dma.options['ctrl']['inc_read'])
            self.assertFalse(dma.options['ctrl']['inc_write'])
        for kwargs in SM.all[-1].inits:
            self.assertFalse(any(kwargs.get(name) is not None for name in
                                 ('out_base', 'set_base', 'sideset_base')))

    def test_rearm_reloads_cleared_isr_and_disables_entire_old_chain_before_close(self):
        guard = self.make_guard()
        guard.arm()
        watcher = SM.all[-1]
        watcher.press_and_release()
        # Interrupt after the first transfer while a follower is still ready
        # to run, exercising cleanup of an in-flight chain rather than only
        # a completely finished STOP.
        self.assertEqual(len(DMA.run_ready_chain(limit=1)), 1)
        self.assertEqual(watcher.isr, 0)
        old = list(DMA.all)
        EVENTS.clear()
        self.assertTrue(guard.arm())
        first_close = next(i for i, event in enumerate(EVENTS) if event[0] == 'dma-close')
        for dma in old:
            self.assertLess(EVENTS.index(('dma-enable', dma.channel, False)), first_close)
            self.assertTrue(dma.closed)
        self.assertEqual(watcher.isr, OUT_LOW)
        self.assertFalse(guard.fired())
        watcher.press_and_release()
        self.assertEqual(len(DMA.run_ready_chain()), 6)
        self.assertTrue(guard.fired())

    def test_held_stop_refuses_rearm_with_outputs_low(self):
        guard = self.make_guard()
        Pin.levels[14] = 0
        self.assertFalse(guard.arm())
        self.assertTrue(guard.fired())
        self.assertTrue(all(self.memory[f._gpio_ctrl] & OUT_LOW for f in self.fans))
        Pin.levels[14] = 1
        self.assertTrue(guard.arm())

    def test_partial_dma_allocation_failure_closes_resources_and_remains_unarmed(self):
        guard = self.make_guard()
        DMA.fail_at = 3
        with self.assertRaisesRegex(RuntimeError, 'DMA allocation failed'):
            guard.arm()
        self.assertTrue(guard.fired())
        self.assertTrue(all(dma.closed for dma in DMA.all))
        self.assertTrue(all(self.memory[f._gpio_ctrl] & OUT_LOW for f in self.fans))

    def test_partial_configuration_failure_disables_enabled_followers_before_close(self):
        guard = self.make_guard()
        DMA.fail_config_at = 3
        with self.assertRaisesRegex(RuntimeError, 'DMA configuration failed'):
            guard.arm()
        self.assertTrue(guard.fired())
        self.assertTrue(all(dma.closed and not dma.enabled for dma in DMA.all))
        self.assertTrue(all(self.memory[f._gpio_ctrl] & OUT_LOW for f in self.fans))

    def test_watcher_claim_does_not_remove_program_while_fans_share_pio2(self):
        guard = self.make_guard()
        self.assertIn(11, Fan._claimed_sms)
        self.assertTrue(all(f.guard is guard for f in self.fans))
        guard.arm()
        guard.close()
        self.assertNotIn(11, Fan._claimed_sms)
        self.assertNotIn(2, PIO.removed)
        self.assertTrue(all(dma.closed for dma in DMA.all))


if __name__ == '__main__':
    unittest.main()
