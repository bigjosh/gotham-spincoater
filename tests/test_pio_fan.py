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
    GPIO_BASE = 0x40028000

    def __init__(self):
        self.data = {}
        self.pio_outputs = {}
        self.pio_directions = {}
        self.gpio_writes = []

    def gpio_status(self, gpio):
        ctrl = self.data.get(self.GPIO_BASE + gpio * 8 + 4, 5)
        pio = ctrl & 31 in (6, 7, 8)
        level = self.pio_outputs.get(gpio, 0) if pio else PinStub.levels.get(gpio, 1)
        output = self.pio_directions.get(gpio, False) if pio else PinStub.modes.get(gpio) == PinStub.OUT
        out_override, oe_override = (ctrl >> 12) & 3, (ctrl >> 14) & 3
        if out_override == 1:
            level = 1 - level
        elif out_override >= 2:
            level = out_override - 2
        if oe_override == 1:
            output = not output
        elif oe_override >= 2:
            output = oe_override == 3
        pad = level if output else PinStub.levels.get(gpio, 1)
        return (pad << 17) | (bool(output) << 13) | (level << 9)

    def __getitem__(self, address):
        if (self.GPIO_BASE <= address < self.GPIO_BASE + 30 * 8
                and (address - self.GPIO_BASE) % 8 == 0 and address not in self.data):
            return self.gpio_status((address - self.GPIO_BASE) // 8)
        return self.data.get(address, 0)

    def __setitem__(self, address, value):
        if self.GPIO_BASE + 0x2000 <= address < self.GPIO_BASE + 0x2000 + 30 * 8:
            return self.__setitem__(address - 0x2000, self[address - 0x2000] | value)
        if self.GPIO_BASE + 0x3000 <= address < self.GPIO_BASE + 0x3000 + 30 * 8:
            return self.__setitem__(address - 0x3000, self[address - 0x3000] & ~value)
        if address & 0xfffff in (8, 0x30):
            self.data[address] = self.data.get(address, 0) & ~value
        else:
            self.data[address] = value
            if self.GPIO_BASE <= address < self.GPIO_BASE + 30 * 8:
                gpio = (address - self.GPIO_BASE) // 8
                self.gpio_writes.append((gpio, value, self.gpio_status(gpio)))


class PinStub:
    OUT, IN, PULL_UP = 1, 0, 2
    levels = {}
    modes = {}
    memory = None

    def __init__(self, number, mode=None, pull=None, value=None):
        self.number = number
        self.levels.setdefault(number, 1)
        self.init(mode, value=value)

    def init(self, mode, value=None):
        if mode is None:
            return
        self.mode = mode
        self.modes[self.number] = mode
        if value is not None:
            self.levels[self.number] = value
        # The SDK GPIO function change used by Pin.init clears overrides.
        self.memory[self.memory.GPIO_BASE + self.number * 8 + 4] = 5

    def value(self):
        ctrl = self.memory[self.memory.GPIO_BASE + self.number * 8 + 4]
        override = (ctrl >> 16) & 3
        if override >= 2:
            return override - 2
        value = (self.memory.gpio_status(self.number) >> 17) & 1
        return 1 - value if override else value


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
        self.fail_init = False
        self.activation_states = []
        self.addr = 0x50200000 + number // 4 * 0x100000 + 0xd4 + number % 4 * 0x18
        self.all.append(self)

    def active(self, value):
        self.enabled = value
        if value and self.initialized:
            gpio = self.options['out_base'].number
            self.activation_states.append((self.memory[self.memory.GPIO_BASE + gpio * 8 + 4],
                                           self.memory.gpio_status(gpio)))

    def init(self, program, **kwargs):
        self.inits += 1
        self.initialized = True
        self.options = kwargs
        self.rx, self.tx = [], []
        self.memory.data[self.addr] = 0
        # Match MicroPython's order: initialize PIO OUT_LOW and direction,
        # then gpio_set_function remuxes and clears all GPIO overrides.
        for field in ('out_base', 'set_base'):
            gpio = kwargs[field].number
            self.memory.pio_outputs[gpio] = 0
            self.memory.pio_directions[gpio] = True
            self.memory[self.memory.GPIO_BASE + gpio * 8 + 4] = 6 + self.number // 4
        if self.fail_init:
            raise RuntimeError('State machine init failed')

    def exec(self, instruction):
        if not self.initialized:
            raise AssertionError('Never execute pin instructions before owning its pin mapping')
        if self.fail_exec:
            raise RuntimeError('Injected instruction failed')
        assert type(instruction) is int, 'Use encoded exec instructions'
        self.execs.append(instruction)
        if instruction == 0xe000:
            self.memory.pio_outputs[self.options['set_base'].number] = 0
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
        self.sleep_hook = None
        PinStub.levels, PinStub.modes, DMAStub.all, SMStub.all, PIOStub.removed = {}, {}, [], [], []
        PinStub.memory = self.memory
        SMStub.memory = self.memory
        program_module, words, _, _ = load_program()
        program_module.combined_program = [words]
        deps = {
            'machine': types.SimpleNamespace(Pin=PinStub, freq=lambda: self.frequency,
                                            mem32=self.memory),
            'rp2': types.SimpleNamespace(DMA=DMAStub, PIO=PIOStub, StateMachine=SMStub),
            'time': types.SimpleNamespace(ticks_us=lambda: self.clock,
                                         sleep_us=self.sleep_us,
                                         ticks_diff=lambda a, b: a - b),
            'periods': types.SimpleNamespace(PeriodMeasurements=MeasurementsStub),
            'combined_program': program_module,
        }
        spec = importlib.util.spec_from_file_location('_pio_fan_test', ROOT / 'device/pio_fan.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, deps):
            spec.loader.exec_module(module)
        self.Fan = module.PioFan

    def sleep_us(self, duration):
        self.clock += duration
        if self.sleep_hook is not None:
            self.sleep_hook(duration)

    def fan(self, pwm=18, tach=19, sm=0, arm=True):
        fan = self.Fan(pwm, tach, sm)
        self.fans.append(fan)
        fan.guard = types.SimpleNamespace(latched=False)
        fan.guard.fired = lambda: fan.guard.latched
        if arm:
            fan.arm()
        return fan

    def real_measurements(self, fan):
        """Use the actual averaging/age implementation for transition tests."""
        spec = importlib.util.spec_from_file_location('_monitor_periods_test', ROOT / 'device/periods.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
                'machine': types.SimpleNamespace(disable_irq=lambda: 0, enable_irq=lambda state: None),
                'time': types.SimpleNamespace(ticks_us=lambda: self.clock,
                                              ticks_diff=lambda a, b: a - b)}):
            spec.loader.exec_module(module)
        fan.measurements = module.PeriodMeasurements()

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
        fan.guard.latched = True  # Shared watcher caught a brief button press.
        self.assertEqual(fan.set_duty(90), 0)
        self.assertFalse(dma.dead)
        self.assertTrue(fan.pwm_is_low())
        self.assertTrue(fan.sample()['stopped'])
        PinStub.levels[14] = 0
        self.assertFalse(fan.arm())
        PinStub.levels[14] = 1
        self.assertFalse(fan.arm())  # Release alone must not acknowledge latch.
        fan.guard.latched = False
        self.assertTrue(fan.arm())
        self.assertEqual(fan._duty, 0)
        self.assertEqual(fan._sm.tx, [fan._command[0]] * 4)
        self.assertFalse(fan.sample()['stopped'])
        self.assertEqual(fan.capture_starts, 1)
        self.assertIs(fan._dma, dma)

    def test_held_stop_at_boot_does_not_execute_unconfigured_pio_pin_writes(self):
        PinStub.levels[14] = 0
        fan = self.fan()
        self.assertTrue(fan.sample()['stopped'])
        self.assertEqual(fan._sm.inits, 1)
        self.assertTrue(fan._sm.enabled)
        self.assertTrue(fan.pwm_is_low())
        fan._sm.rx = [30, 100, 100]
        self.assertTrue(fan.sample()['valid'])

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

    def test_monitor_holds_active_low_and_samples_with_physical_stop_held(self):
        fan = self.fan()
        fan.set_duty(75)
        self.memory.pio_outputs[18] = 1  # Model a running high phase before stop.
        PinStub.levels[14] = 0
        self.memory.gpio_writes.clear()
        self.assertTrue(fan.monitor())
        self.assertTrue(fan._stopped)
        self.assertTrue(fan._monitoring)
        self.assertNotIn('in_base', fan._sm.options)
        self.assertEqual(fan._sm.options['jmp_pin'].number, 19)
        self.assertEqual(self.memory[fan._gpio_ctrl] & 0x3f000, 0x2000)
        self.assertEqual(fan._sm.activation_states[-1][0] & 0x3f000, 0x2000)
        self.assertTrue(fan.pwm_is_low())
        self.assertEqual(fan._output.value(), 0)  # No input override is needed now.
        self.assertEqual(self.memory[self.memory.GPIO_BASE + 14 * 8 + 4] & 0x3f000, 0)
        writes = [status for gpio, _, status in self.memory.gpio_writes if gpio == 18]
        self.assertTrue(writes)
        self.assertTrue(all(status & 0x22200 == 0x2000 for status in writes))
        fan._sm.rx = [30, 100, 100]
        reading = fan.sample()
        self.assertTrue(reading['stopped'])
        self.assertTrue(reading['monitoring'])
        self.assertTrue(reading['valid'])
        self.assertIsNone(reading['fault'])

    def test_monitor_keeps_existing_capture_when_stop_was_held_at_boot(self):
        PinStub.levels[14] = 0
        fan = self.fan()
        self.assertEqual(fan._sm.inits, 1)
        self.assertTrue(fan.monitor())
        self.assertEqual(fan._sm.inits, 1)
        self.assertTrue(fan._stopped)
        self.assertTrue(fan._monitoring)
        self.assertTrue(fan.pwm_is_low())
        fan._sm.rx = [30, 100, 100]
        self.assertTrue(fan.sample()['valid'])

    def test_monitor_is_idempotent_and_positive_commands_cannot_unlock_output(self):
        fan = self.fan()
        fan.stop()
        fan.monitor()
        fan._sm.rx = [30, 100, 100]
        self.assertTrue(fan.sample()['valid'])
        dma, inits, records = fan._dma, fan._sm.inits, len(fan.measurements.records)
        command = fan._command[0]
        for _ in range(5):
            self.assertTrue(fan.monitor())
            self.assertEqual(fan.set_duty(100), 0)
        self.assertIs(fan._dma, dma)
        self.assertEqual(fan._sm.inits, inits)
        self.assertEqual(len(fan.measurements.records), records)
        self.assertEqual(fan._command[0], command)
        # Even erroneous internal PIO output data cannot override a passive pad.
        self.memory.pio_outputs[18] = 1
        self.assertTrue(fan.pwm_is_low())
        self.assertEqual(fan.sample()['duty'], 0)

    def test_stop_keeps_sampler_dma_and_partial_period_capture_alive(self):
        fan = self.fan()
        self.real_measurements(fan)
        fan._sm.rx = [30, 100, 100]
        self.assertEqual(fan.sample()['rpm'], 3000)
        fan.stop()
        self.assertTrue(fan._sm.enabled)
        self.assertTrue(fan._monitoring)
        self.assertFalse(fan._dma.dead)
        self.assertTrue(fan.pwm_is_low())
        reading = fan.sample()
        self.assertTrue(reading['valid'])
        self.assertEqual(reading['rpm'], 3000)
        self.clock += 1500000
        self.assertFalse(fan.sample()['valid'])

    def test_monitor_reports_new_coasting_periods_without_faking_zero_at_stop(self):
        fan = self.fan()
        self.real_measurements(fan)
        fan._sm.rx = [30, 100, 100]
        self.assertEqual(fan.sample()['rpm'], 3000)
        fan.stop()
        self.clock += 20000
        fan.monitor()
        self.assertEqual(fan.sample()['rpm'], 3000)
        fan._sm.rx = [200] * 8
        self.clock += 20000
        reading = fan.sample()
        self.assertTrue(reading['valid'])
        self.assertEqual(reading['rpm'], 1500)
        self.assertEqual(reading['age_ms'], 0)
        self.assertTrue(reading['stopped'])
        self.assertTrue(fan.pwm_is_low())

    def test_only_explicit_arm_unlocks_pwm_without_resetting_capture(self):
        fan = self.fan()
        fan.monitor()
        PinStub.levels[14] = 0
        self.assertFalse(fan.arm())
        self.assertTrue(fan._sm.enabled)
        self.assertTrue(fan.pwm_is_low())
        self.assertTrue(fan.monitor())
        PinStub.levels[14] = 1
        self.assertTrue(fan.arm())
        self.assertFalse(fan._monitoring)
        self.assertFalse(fan._stopped)
        self.assertNotIn('in_base', fan._sm.options)
        self.assertEqual(self.memory[fan._gpio_ctrl] & 0x3f000, 0)
        self.assertTrue(fan.pwm_is_low())
        self.assertEqual(fan.set_duty(75), 75)
        fan.guard.latched = True
        self.assertTrue(fan.sample()['stopped'])
        self.assertTrue(fan._sm.enabled)
        self.assertTrue(fan.pwm_is_low())
        self.assertEqual(fan.capture_starts, 1)

    def test_six_passive_channels_reuse_their_existing_program_and_dma_pacing(self):
        pins = ((18, 19), (20, 21), (22, 28), (1, 0), (13, 12), (16, 17))
        fans = [self.fan(*pair, sm) for pair, sm in zip(pins, (0, 1, 2, 3, 8, 9))]
        for fan in fans:
            self.assertTrue(fan.monitor())
        self.assertEqual(len(self.Fan._claimed_sms), 6)
        self.assertEqual([fan._dma.configured['ctrl']['treq_sel'] for fan in fans],
                         [0, 1, 2, 3, 16, 17])
        self.assertEqual(sum(not dma.dead for dma in DMAStub.all), 6)
        self.assertTrue(all(fan.pwm_is_low() for fan in fans))
        self.assertEqual(PIOStub.removed, [])
        fans[0].arm()
        self.assertTrue(all(fan._monitoring and fan.pwm_is_low() for fan in fans[1:]))

    def test_monitor_does_not_clear_or_restart_a_hardware_driver_error(self):
        fan = self.fan()
        self.memory.data[fan._irq] = fan._bit
        self.assertEqual(fan.sample()['fault'], 'command_underrun')
        inits = fan._sm.inits
        self.assertFalse(fan.monitor())
        self.assertEqual(fan._sm.inits, inits)
        # A hardware fault is invalid/parked; monitor never tries to repair it.
        self.assertTrue(fan.pwm_is_low())
        self.assertTrue(fan.arm())
        self.assertEqual(fan.capture_starts, 2)

    def test_abnormal_retry_configuration_exception_stays_low_and_releases_dma(self):
        fan = self.fan()
        self.memory.data[fan._irq] = fan._bit
        fan.sample()
        fan._sm.fail_init = True
        with self.assertRaisesRegex(RuntimeError, 'init failed'):
            fan.arm()
        self.assertFalse(fan._sm.enabled)
        self.assertIsNone(fan._dma)
        self.assertTrue(fan.pwm_is_low())
        fan._sm.fail_init = False

    def test_lost_monitor_override_reports_driver_error_and_hard_stops(self):
        fan = self.fan()
        fan.monitor()
        self.memory[fan._gpio_ctrl] &= ~0x3f000
        reading = fan.sample()
        self.assertEqual(reading['fault'], 'monitor_output_override_lost')
        self.assertTrue(reading['stopped'])
        self.assertTrue(reading['monitoring'])
        self.assertTrue(fan.pwm_is_low())
        self.assertTrue(fan._sm.enabled)

    def test_corrupted_invert_override_is_repaired_to_low_not_forced_high(self):
        fan = self.fan()
        fan.monitor()
        self.memory[fan._gpio_ctrl] = ((self.memory[fan._gpio_ctrl] & ~(3 << 12))
                                      | (1 << 12))
        reading = fan.sample()
        self.assertEqual(reading['fault'], 'monitor_output_override_lost')
        self.assertEqual(self.memory[fan._gpio_ctrl] & (3 << 12), 2 << 12)
        self.assertTrue(fan.pwm_is_low())

    def test_raw_pad_check_rejects_floating_or_externally_high_output(self):
        fan = self.fan()
        fan.monitor()
        self.assertTrue(fan.pwm_is_low())
        self.memory.data[fan._gpio_status] = 0
        self.assertFalse(fan.pwm_is_low())
        self.memory.data[fan._gpio_status] = (1 << 13) | (1 << 17)
        self.assertFalse(fan.pwm_is_low())
        del self.memory.data[fan._gpio_status]

    def test_close_closes_dma_even_when_injected_instruction_raises(self):
        fan = self.fan()
        dma = fan._dma
        fan.set_duty(75)
        fan._sm.fail_exec = True
        with self.assertRaises(RuntimeError):
            fan.close()
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
            official.asm_pio_encode('set(y, 17)', 0),
            official.asm_pio_encode('mov(isr, y)', 0)])
        # The real encoder rejects JMP strings. The hardware accepts its
        # numeric opcode directly, so never pass this expression to exec().
        with self.assertRaises(TypeError):
            official.asm_pio_encode('jmp(6)', 0)
        fan.close()
        self.assertEqual(fan._sm.execs[-2:], [
            official.asm_pio_encode('set(pins, 0)', 0), 3])

    def test_boot_cannot_enable_power_before_shared_guard_is_attached_and_armed(self):
        fan = self.fan(arm=False)
        fan.guard = None
        self.assertFalse(fan.arm())
        self.assertEqual(fan.set_duty(100), 0)
        self.assertTrue(fan.pwm_is_low())
        self.assertEqual(fan.capture_starts, 1)

    def test_arm_drains_old_commands_under_low_override_without_touching_capture(self):
        fan = self.fan()
        fan.set_duty(75)
        self.memory.pio_outputs[18] = 1
        fan._sm.rx = [30, 100, 100]
        fan.sample()
        inits, execs, dma, records = fan._sm.inits, list(fan._sm.execs), fan._dma, list(fan.measurements.records)
        def while_draining(duration):
            self.assertGreaterEqual(duration, 500)
            self.assertTrue(fan.pwm_is_low())
            self.assertEqual(fan._command[0], fan._zero_command)
            fan._sm.rx.append(150)  # New complete period during START.
            self.memory.pio_outputs[18] = 0  # Zero reaches the PIO output.
        self.sleep_hook = while_draining
        fan.stop()
        self.assertTrue(fan.arm())
        self.assertTrue(fan.pwm_is_low())
        self.assertEqual(fan._sm.inits, inits)
        self.assertEqual(fan._sm.execs, execs)
        self.assertIs(fan._dma, dma)
        self.assertEqual(fan.measurements.records, records)
        fan.sample()
        self.assertEqual(fan.measurements.records, records + [15000])

    def test_guard_firing_during_arm_never_unlocks_output(self):
        fan = self.fan()
        self.memory.pio_outputs[18] = 1
        self.sleep_hook = lambda duration: setattr(fan.guard, 'latched', True)
        self.assertFalse(fan.arm())
        self.assertTrue(fan.pwm_is_low())
        self.assertEqual(fan.set_duty(100), 0)
        self.assertEqual(fan.capture_starts, 1)


if __name__ == '__main__':
    unittest.main()
