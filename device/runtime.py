"""Core-1 control owner; core 0 consumes snapshots and edits idle recipes."""
import _thread
from machine import Pin
from time import ticks_ms, ticks_us, ticks_diff, ticks_add, sleep_ms
from control import ControlEngine
from pio_fan import PioFan
import config


class _Button:
    def __init__(self, gpio):
        self.pin = Pin(gpio, Pin.IN, Pin.PULL_UP)
        self.raw = self.stable = self.pin.value()
        self.changed = ticks_ms()

    def pressed(self, now):
        value = self.pin.value()
        if value != self.raw:
            self.raw, self.changed = value, now
        if value != self.stable and ticks_diff(now, self.changed) >= 30:
            self.stable = value
            return value == 0
        return False


class Controller:
    def __init__(self, book):
        self.book = book
        self.lock = _thread.allocate_lock()
        self.editing = False
        self.closing = False
        self.finished = False
        self.started = False
        self._closed = False
        self.error = ''
        self.heartbeat = ticks_ms()
        self.max_lag_ms = 0
        self.max_tick_us = 0
        self._running = False
        self._profile = book.selectedprofile()
        self._settings = book.settings()
        self._snapshot = None
        self.fans = {}
        self.enabled = tuple(config.ENABLED_CHANNELS)
        if (not self.enabled or len(set(self.enabled)) != len(self.enabled) or
                any(type(i) is not int or not 0 <= i < 6 for i in self.enabled)):
            raise ValueError('Enable one or more unique fan channels 0..5')
        if any(i >= 4 for i in self.enabled) and not config.AUX_LINKS_DISCONNECTED:
            raise ValueError('Disconnect kit RGB/buzzer/D1/D2 links before enabling fans 4/5')
        numbers = [n for i in self.enabled for n in config.FUTURE_CHANNELS[i]]
        if len(set(numbers)) != len(numbers):
            raise ValueError('Fan GPIO allocation overlaps')
        self.start_button = _Button(config.BUTTON_START)
        self.stop_pin = Pin(config.BUTTON_STOP, Pin.IN, Pin.PULL_UP)
        try:
            # Allocate fan PIO before starting Wi-Fi so its driver can select a
            # remaining PIO block. No fan starts at nonzero power here.
            for i in self.enabled:
                pwm, tach = config.FUTURE_CHANNELS[i]
                self.fans[i] = PioFan(pwm, tach,
                    sm_id=config.TACH_STATE_MACHINES[i], stop_pin=config.BUTTON_STOP,
                    window=config.PERIOD_AVERAGE, pulses_per_rev=config.PULSES_PER_REV)
            self.engine = ControlEngine(enabled=self.enabled)
            self._snapshot = self.engine.snapshot()
        except BaseException:
            for fan in self.fans.values():
                fan.close()
            raise

    def start_thread(self):
        if self.started:
            raise RuntimeError('Controller already started')
        _thread.stack_size(12288)
        # Display/radio initialization may take longer than the heartbeat
        # interval between construction and actually starting this worker.
        self.heartbeat = ticks_ms()
        self.started = True
        try:
            _thread.start_new_thread(self._run, ())
        except BaseException:
            self.started = False
            raise

    def _stop_outputs(self):
        """Driver teardown: worker only, or core 0 after worker acknowledgement."""
        for fan in self.fans.values():
            try:
                fan.stop()
            except Exception as error:
                self.error = str(error)

    def request_shutdown(self, reason=None):
        """Core-0-safe shutdown request, without touching live DMA/SM objects.

        The kit's active-low button shorts this line to ground, so actively
        holding it LOW is electrically compatible. PIO observes the asserted
        input and parks independently of either Python core. Do not release it
        until the worker acknowledges and every output has been closed LOW.

        MicroPython v1.29 rp2_pio.c:630-633 configures in_base only in PINCTRL;
        GPIO initialization at 671-691 covers OUT/SET/SIDESET and isolated
        JMP pins. Consequently an in-flight fan.arm()/sm.init(in_base=STOP)
        cannot remux or release this SIO-driven STOP line (tach is JMP_PIN).
        """
        self.closing = True
        self.stop_pin.init(Pin.OUT, value=0)
        if reason and not self.error:
            self.error = str(reason)

    def _run(self):
        next_tick = ticks_ms()
        last_publish = ticks_add(next_tick, -100)
        was_running = False
        try:
            while not self.closing:
                now = ticks_ms()
                delay = ticks_diff(next_tick, now)
                if delay > 0:
                    sleep_ms(min(delay, 2))
                    continue
                began = ticks_us()
                lag = max(0, ticks_diff(now, next_tick))
                self.max_lag_ms = max(self.max_lag_ms, lag)
                # Do not perform a burst of catch-up power adjustments.
                next_tick = ticks_add(now, 20)
                start = self.start_button.pressed(now)
                if not self.stop_pin.value():
                    if self.engine.state != 'STOPPED':
                        self.engine.stop('STOP button')
                        self._stop_outputs()
                elif start:
                    with self.lock:
                        can_start = not self.editing and not self._running and not self.closing
                        profile, settings = self._profile, self._settings
                        if can_start:
                            # Marks the transition before idle flash saves can
                            # claim the recipe store on the other core.
                            self._running = True
                    if can_start:
                        try:
                            for fan in self.fans.values():
                                if self.closing:
                                    raise RuntimeError('Controller shutting down')
                                if not fan.arm():
                                    raise RuntimeError('STOP held or fan could not arm')
                                if self.closing:
                                    raise RuntimeError('Controller shutting down')
                            self.engine.start(profile, settings, now)
                            if self.closing:
                                raise RuntimeError('Controller shutting down')
                            was_running = True
                        except Exception as error:
                            self.engine.stop('Cannot start: ' + str(error))
                            self._stop_outputs()

                readings = {i: fan.sample() for i, fan in self.fans.items()}
                duties = self.engine.update(now, readings)
                if self.closing:
                    self.engine.stop('Controller shutting down')
                running = self.engine.running
                if self.engine.state in ('STOPPED', 'FAULT'):
                    # Also propagates any individual PIO latch to all channels.
                    self._stop_outputs()
                else:
                    for i, fan in self.fans.items():
                        if self.closing:
                            self.engine.stop('Controller shutting down')
                            self._stop_outputs()
                            running = False
                            break
                        fan.set_duty(duties[i])
                with self.lock:
                    self._running = running
                if ticks_diff(now, last_publish) >= 100 or running != was_running:
                    state = self.engine.snapshot()
                    for i, reading in readings.items():
                        state['fans'][i]['fault'] = reading.get('fault')
                        state['fans'][i]['tach_overflows'] = reading.get('overflows', 0)
                        state['fans'][i]['age_ms'] = reading.get('age_ms')
                    state['loop_lag_ms'] = self.max_lag_ms
                    state['tick_us'] = self.max_tick_us
                    state['control_hz'] = 50
                    with self.lock:
                        self._snapshot = state
                    last_publish = now
                was_running = running
                self.max_tick_us = max(self.max_tick_us, ticks_diff(ticks_us(), began))
                self.heartbeat = ticks_ms()
        except BaseException as error:
            self.error = str(error)
            self.engine.stop('Controller error: ' + str(error))
            state = self.engine.snapshot()
            state['state'] = 'FAULT'
            state['message'] = self.error
            with self.lock:
                self._running = False
                self._snapshot = state
        finally:
            self._stop_outputs()
            with self.lock:
                self._running = False
            self.finished = True

    def snapshot(self):
        with self.lock:
            result = dict(self._snapshot)
        # The lists nested in this immutable published snapshot are read-only.
        return result

    def save_config(self, value):
        with self.lock:
            if self._running or self.editing or self.closing:
                raise RuntimeError('Stop the recipe before editing')
            self.editing = True
        try:
            self.book.save(value)
            profile, settings = self.book.selectedprofile(), self.book.settings()
            with self.lock:
                self._profile, self._settings = profile, settings
            return self.book.data
        finally:
            self.editing = False

    def close(self):
        if self._closed:
            return
        self.request_shutdown()
        if self.started:
            began = ticks_ms()
            while not self.finished and ticks_diff(ticks_ms(), began) < 2000:
                sleep_ms(5)
        if self.started and not self.finished:
            # STOP remains asserted. Do not mutate any driver/DMA state while
            # the worker could still be returning from arm/sample/set_duty.
            raise RuntimeError('Control worker did not stop; hardware STOP held LOW')
        first_error = None
        for fan in self.fans.values():
            try:
                fan.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            # Keep STOP asserted if any output/resource cleanup failed.
            raise first_error
        self.stop_pin.init(Pin.IN, Pin.PULL_UP)
        self._closed = True
