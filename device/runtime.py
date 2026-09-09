"""Core-1 control owner; core 0 consumes snapshots and edits idle recipes."""
import _thread
from machine import Pin
from time import ticks_ms, ticks_us, ticks_diff, ticks_add, sleep_ms
from control import ControlEngine
from fan_settings import FanSettings
from pio_fan import PioFan
import config


class _Button:
    def __init__(self, gpio):
        self.pin = Pin(gpio, Pin.IN, Pin.PULL_UP)
        self.raw = self.stable = self.pin.value()
        self.changed = ticks_ms()
        self.blocked = False

    def block_until_release(self):
        self.blocked = True

    def pressed(self, now):
        value = self.pin.value()
        if value != self.raw:
            self.raw, self.changed = value, now
        pressed = False
        if value != self.stable and ticks_diff(now, self.changed) >= 30:
            self.stable = value
            pressed = value == 0
        if self.blocked:
            if value == 1 and self.stable == 1:
                self.blocked = False
            return False
        return pressed


class Controller:
    EDIT_TIMEOUT_MS = 1000

    def __init__(self, book, fan_settings=None):
        self.book = book
        self.lock = _thread.allocate_lock()
        self.editing = False
        self._edit_ready = False
        self._pending_enabled = None
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
        self.available = tuple(range(6 if config.AUX_LINKS_DISCONNECTED else 4))
        self.fan_settings = fan_settings or FanSettings(
            defaults=config.ENABLED_CHANNELS, available=self.available)
        self.enabled = tuple(i for i in self.fan_settings.load() if i in self.available)
        self._outputs_parked = False
        self._driver_errors = [None] * 6
        self._error_parked = set()
        numbers = [n for i in self.available for n in config.FUTURE_CHANNELS[i]]
        if len(set(numbers)) != len(numbers):
            raise ValueError('Fan GPIO allocation overlaps')
        self.start_button = _Button(config.BUTTON_START)
        self.stop_pin = Pin(config.BUTTON_STOP, Pin.IN, Pin.PULL_UP)
        try:
            # Allocate fan PIO before starting Wi-Fi so its driver can select a
            # remaining PIO block. No fan starts at nonzero power here.
            for i in self.available:
                pwm, tach = config.FUTURE_CHANNELS[i]
                self.fans[i] = PioFan(pwm, tach,
                    sm_id=config.TACH_STATE_MACHINES[i], stop_pin=config.BUTTON_STOP,
                    window=config.PERIOD_AVERAGE, pulses_per_rev=config.PULSES_PER_REV)
                if i not in self.enabled:
                    self.fans[i].stop()
            self.engine = ControlEngine(enabled=self.enabled)
            self._snapshot = self._state()
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
        successful = True
        for fan in self.fans.values():
            try:
                fan.stop()
            except Exception as error:
                self.error = str(error)
                successful = False
        self._outputs_parked = True
        return successful

    def _state(self):
        state = self.engine.snapshot()
        for i, fan in enumerate(state['fans']):
            fan['available'] = i in self.available
            fan['unavailable_reason'] = ('' if fan['available'] else
                'Disconnect kit RGB/buzzer/D1/D2 links before enabling fans 4/5')
            fan['driver_error'] = self._driver_errors[i] if i in self.enabled else None
            if fan['driver_error'] is not None:
                # A command failure can occur after this tick's engine update.
                # Always publish the actual parked output, not its old request.
                fan['duty'] = 0.0
                fan['rpm'] = None
                fan['valid'] = False
        state['fan_settings_source'] = self.fan_settings.source
        state['fan_settings_error'] = self.fan_settings.load_error
        return state

    def _park_channel(self, channel):
        """Worker-only isolation; failed teardown requires the shared STOP."""
        if channel in self._error_parked:
            return
        try:
            self.fans[channel].stop()
        except Exception as error:
            reason = 'Could not stop fan %d: %s' % (channel, error)
            self.request_shutdown(reason)
            raise RuntimeError(reason)
        self._error_parked.add(channel)

    def _isolate_fan(self, channel, reason):
        """Park an unusable driver; ordinary RPM warnings never call here."""
        self._park_channel(channel)
        if self._driver_errors[channel] is None:
            self._driver_errors[channel] = str(reason)

    def _service_edit(self):
        """Worker-only pause/commit acknowledgement, with every PWM held LOW."""
        # A START edge can still be inside its debounce window when flash IO
        # finishes. Require a release before recognizing another press.
        self.start_button.block_until_release()
        if not self._edit_ready:
            if not self._stop_outputs():
                self.request_shutdown('Could not stop outputs for editing')
                raise RuntimeError('Could not stop outputs for editing')
            with self.lock:
                self._edit_ready = True
        with self.lock:
            pending = self._pending_enabled
        if pending is not None:
            self.engine.set_enabled(pending)
            self.enabled = pending
            if not self.stop_pin.value():
                self.engine.stop('STOP button')
            state = self._state()
            with self.lock:
                self._snapshot = state
                self._pending_enabled = None

    def _wait_edit(self, applied=False):
        began = ticks_ms()
        while True:
            with self.lock:
                ready = (self._pending_enabled is None if applied else self._edit_ready)
            if ready and not self.closing:
                return
            if (self.closing or self.finished or
                    ticks_diff(ticks_ms(), began) >= self.EDIT_TIMEOUT_MS):
                # The worker might still be between driver operations. Hold
                # hardware STOP rather than attempting a core-0 teardown.
                self.request_shutdown('Fan settings worker acknowledgement failed')
                raise RuntimeError('Controller stopped: fan settings acknowledgement failed')
            sleep_ms(2)

    def _begin_edit(self):
        with self.lock:
            if self._running or self.editing or self.closing:
                raise RuntimeError('Stop the recipe before editing')
            self.editing = True
            self._edit_ready = False
        try:
            if self.started:
                self._wait_edit()
            else:
                # Construction/host tests: no worker exists yet, so core 0 is
                # still the sole driver owner.
                self._service_edit()
        except BaseException:
            self._end_edit()
            raise

    def _end_edit(self):
        with self.lock:
            self.editing = False
            self._edit_ready = False

    def set_fan_enabled(self, channel, enabled):
        """Persist an idle selection on core 0; core 1 applies and publishes it."""
        if type(channel) is not int or not 0 <= channel < 6 or type(enabled) is not bool:
            raise ValueError('Choose a fan from 0 to 5 and a boolean enabled setting')
        if channel not in self.available:
            raise ValueError('Fan %d unavailable: disconnect the kit links first' % channel)
        self._begin_edit()
        try:
            candidate = tuple(i for i in self.available
                              if (enabled if i == channel else i in self.enabled))
            if candidate != self.enabled:
                try:
                    self.fan_settings.save(candidate)
                except Exception:
                    if self.fan_settings.uncertain:
                        self.request_shutdown('Fan settings storage outcome uncertain')
                    raise
                with self.lock:
                    self._pending_enabled = candidate
                if self.started:
                    self._wait_edit(applied=True)
                else:
                    self._service_edit()
            return self.snapshot()
        finally:
            self._end_edit()

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
                if self.editing:
                    self._service_edit()
                    # START presses sampled while editing are consumed. Keep
                    # STOP handling and the heartbeat alive during flash IO.
                    self.heartbeat = ticks_ms()
                    continue
                if self.stop_pin.value() and start:
                    with self.lock:
                        can_start = not self.editing and not self._running and not self.closing
                        profile, settings = self._profile, self._settings
                        if can_start:
                            # Marks the transition before idle flash saves can
                            # claim the recipe store on the other core.
                            self._running = True
                    if can_start:
                        try:
                            if not self.enabled:
                                raise RuntimeError('Enable at least one fan before START')
                            self._driver_errors = [None] * 6
                            self._error_parked.clear()
                            for i in self.enabled:
                                fan = self.fans[i]
                                if self.closing:
                                    raise RuntimeError('Controller shutting down')
                                if not self.stop_pin.value():
                                    raise RuntimeError('STOP held')
                                arm_error = None
                                try:
                                    armed = fan.arm()
                                except Exception as error:
                                    arm_error = 'arm failed: ' + str(error)
                                if self.closing:
                                    raise RuntimeError('Controller shutting down')
                                if not self.stop_pin.value():
                                    raise RuntimeError('STOP held')
                                if arm_error:
                                    self._isolate_fan(i, arm_error)
                                elif not armed:
                                    # False means STOP was sampled or the CPU
                                    # clock changed. A brief STOP may already
                                    # be released, so this remains global.
                                    raise RuntimeError('STOP or clock change prevented arming')
                            self._outputs_parked = False
                            self.engine.start(profile, settings, now)
                            if self.closing:
                                raise RuntimeError('Controller shutting down')
                            if not self.stop_pin.value():
                                raise RuntimeError('STOP held')
                            was_running = True
                        except Exception as error:
                            if self.closing and self.error:
                                raise
                            self.engine.stop('Cannot start: ' + str(error))
                            self._stop_outputs()

                readings = {}
                if not self._outputs_parked:
                    for i in self.enabled:
                        if self._driver_errors[i] is not None:
                            readings[i] = {'driver_error': self._driver_errors[i], 'valid': False}
                            continue
                        try:
                            reading = self.fans[i].sample()
                        except Exception as error:
                            self._isolate_fan(i, 'sample failed: ' + str(error))
                            readings[i] = {'driver_error': self._driver_errors[i], 'valid': False}
                            continue
                        if reading.get('fault') == 'clock_changed':
                            # A changed CPU clock invalidates every channel's
                            # timing, rather than one fan's measurement.
                            raise RuntimeError('Controller clock changed')
                        if reading.get('fault'):
                            # PIO resource errors are distinct from tach/RPM
                            # warnings. A stopped broken driver must not be
                            # mistaken for the shared physical STOP latch.
                            self._isolate_fan(i, reading['fault'])
                            readings[i] = {'driver_error': self._driver_errors[i], 'valid': False}
                        else:
                            readings[i] = reading
                if self.closing or not self.stop_pin.value():
                    self.engine.stop('Controller shutting down' if self.closing else 'STOP button')
                    self._stop_outputs()
                    readings = {}
                duties = self.engine.update(now, readings)
                if self.closing:
                    self.engine.stop('Controller shutting down')
                if self.engine.state == 'STOPPED':
                    self._stop_outputs()
                elif not self._outputs_parked:
                    for i in self.enabled:
                        if self.closing or not self.stop_pin.value():
                            self.engine.stop('Controller shutting down' if self.closing else 'STOP button')
                            self._stop_outputs()
                            break
                        if self._driver_errors[i] is not None:
                            continue
                        try:
                            self.fans[i].set_duty(duties[i])
                        except Exception as error:
                            self._isolate_fan(i, 'power command failed: ' + str(error))
                running = self.engine.running
                with self.lock:
                    self._running = running
                if ticks_diff(now, last_publish) >= 100 or running != was_running:
                    state = self._state()
                    for i, reading in readings.items():
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
            self.request_shutdown('Controller error: ' + str(error))
            self.engine.stop('Controller error: ' + str(error))
            state = self._state()
            state['state'] = 'ERROR'
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
        self._begin_edit()
        try:
            self.book.save(value)
            profile, settings = self.book.selectedprofile(), self.book.settings()
            with self.lock:
                self._profile, self._settings = profile, settings
            return self.book.data
        finally:
            self._end_edit()

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
