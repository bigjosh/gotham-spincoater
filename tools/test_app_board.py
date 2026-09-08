"""Live default recipe with core1, TFT, open AP and external HTTP/DNS traffic.

Uses a disposable recipe file and a scripted START after 20 seconds so a
host can join the AP and test idle saving. Physical STOP remains live.
Never changes the user's saved recipes or enabled channels.
"""
# Reboot/launch the current firmware before testing. Reuse its modules;
# reimporting the large HTML constant repeatedly fragments the device heap.
import os, gc
from time import ticks_ms, ticks_diff, sleep_ms
from recipes import RecipeBook
from runtime import Controller
from display import Display
from dashboard import Dashboard
from portal import Portal
from main import start_access_point
from control import ControlEngine
import config

assert ControlEngine.SEEK_GAIN == 0.01
controller = ap = portal = None
try:
    book = RecipeBook('_bench_recipes.json')
    controller = Controller(book)
    dashboard = Dashboard(Display(rotation=config.DISPLAY_ROTATION,
        inversion=config.DISPLAY_INVERSION, bgr=config.DISPLAY_BGR))

    def status():
        value = controller.snapshot()
        value.update(ssid=config.WIFI_SSID, ip=config.WIFI_IP)
        return value

    ap = start_access_point()
    portal = Portal(lambda: book.data, controller.save_config, status)
    dashboard.set_yield_hook(portal.poll)
    began = ticks_ms()

    class StartOnce:
        def __init__(self):
            self.used = False
        def pressed(self, now):
            if self.used or ticks_diff(now, began) < 20000:
                return False
            self.used = True
            return True

    controller.start_button = StartOnce()
    controller.start_thread()
    assert ap.active()
    print('GOTHAM_TEST_READY', ap.config('ssid'), ap.ifconfig(), 'START in20s')
    last_display = ticks_ms() - 200
    last_report = -1000
    while ticks_diff(ticks_ms(), began) < 115000:
        portal.poll()
        now = ticks_ms()
        assert not controller.finished, controller.error
        assert ticks_diff(now, controller.heartbeat) < 500, 'Control heartbeat'
        elapsed = ticks_diff(now, began)
        value = status()
        if ticks_diff(now, last_display) >= 200:
            dashboard.update(value)
            last_display = now
        if elapsed - last_report >= 1000:
            fan = value['fans'][0]
            print('APP t=%.1f %s target=%.0f rpm=%s duty=%.1f lag=%dms tick=%dus overflow=%d heap=%d' %
                  (elapsed / 1000, value['state'], value['target_rpm'],
                   str(round(fan['rpm'])) if fan['valid'] else '--', fan['duty'],
                   value.get('loop_lag_ms', 0), value.get('tick_us', 0),
                   fan.get('tach_overflows', 0), gc.mem_free()))
            gc.collect()
            last_report = elapsed
        if value['state'] in ('COMPLETE', 'FAULT', 'STOPPED'):
            break
        sleep_ms(2)
    print('APP_RESULT', status())
    assert status()['state'] == 'COMPLETE', status()
    print('FULL_APP_BOARD_PASS')
finally:
    if controller is not None:
        controller.close()
    if portal is not None:
        portal.close()
    if ap is not None:
        ap.active(False)
    for path in ('_bench_recipes.json', '_bench_recipes.json.bak', '_bench_recipes.json.tmp'):
        try:
            os.remove(path)
        except OSError:
            pass
