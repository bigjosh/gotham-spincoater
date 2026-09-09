"""Pico integration check for fan selection; never commands nonzero PWM.

Run through the identity-guarded pico.ps1 Exec helper after deployment, with
the normal app stopped. Uses separate temporary settings, preserves fans.json
and recipes.json, and leaves outputs LOW. Reboot into the normal app afterward.
"""
import os
from time import sleep_ms, ticks_ms, ticks_diff
from machine import Pin
from recipes import RecipeBook
from fan_settings import FanSettings
from runtime import Controller
import config


path = '_fan_selection_smoke.json'
temporary_files = (path, path + '.bak', path + '.tmp', path + '.bak.tmp')
if any(name in os.listdir() for name in temporary_files):
    raise RuntimeError('Temporary selection-test files already exist; inspect before running')

settings = FanSettings(defaults=(),
                       available=tuple(range(6 if config.AUX_LINKS_DISCONNECTED else 4)),
                       path=path)
controller = None
try:
    controller = Controller(RecipeBook(), fan_settings=settings)
    controller.start_thread()
    sleep_ms(150)
    selections = ((0,), (), (0, 1), (0, 1, 2, 3), (1, 2, 3))
    if config.AUX_LINKS_DISCONNECTED:
        selections += ((0, 1, 2, 3, 4, 5), (4, 5))
    for selected in selections + ((),):
        for channel in controller.available:
            controller.set_fan_enabled(channel, channel in selected)
        state = controller.snapshot()
        actual = tuple(i for i, fan in enumerate(state['fans']) if fan['enabled'])
        assert actual == selected, (actual, selected)
        assert not state['running'], state
        assert all(fan['duty'] == 0 for fan in state['fans']), state
        assert all(Pin(config.FUTURE_CHANNELS[i][0]).value() == 0
                   for i in controller.available), 'PWM pad not LOW'
        assert not controller.finished and not controller.closing, controller.error
        assert ticks_diff(ticks_ms(), controller.heartbeat) < 500, 'Worker heartbeat stalled'
        recovered = FanSettings(defaults=(0,), available=controller.available, path=path)
        assert recovered.load() == selected, 'Selection did not survive reload'
        print('SELECTION_OK enabled=%s LOW=True saved=True heartbeat=True' % (selected,))
    if not config.AUX_LINKS_DISCONNECTED:
        for channel in (4, 5):
            try:
                controller.set_fan_enabled(channel, True)
                raise AssertionError('Occupied GPIO channel accepted')
            except ValueError:
                pass
        print('GPIO_LOCKS_OK channels=4,5')
    print('FAN_SELECTION_BOARD_OK all outputs zero; no recipe started')
finally:
    if controller is not None:
        controller.close()
    for name in temporary_files:
        try:
            os.remove(name)
        except OSError as error:
            if not error.args or error.args[0] != 2:
                raise
