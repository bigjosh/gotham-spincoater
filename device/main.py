"""Gotham Spinner: core-1 fan control, core-0 dashboard and captive portal."""
from time import ticks_ms, ticks_diff, sleep_ms
import gc
import sys
import config
from recipes import RecipeBook
from runtime import Controller
from display import Display
from dashboard import Dashboard
from portal import Portal


def start_access_point():
    import network
    ap = network.WLAN(network.WLAN.IF_AP)
    ap.active(False)
    ap.config(ssid=config.WIFI_SSID, security=0, key='', channel=6)
    ap.ifconfig((config.WIFI_IP, '255.255.255.0', config.WIFI_IP, config.WIFI_IP))
    ap.active(True)
    return ap


def run(duration_ms=None):
    book = RecipeBook('recipes.json')
    book.load()
    controller = None
    ap = None
    portal = None
    try:
        controller = Controller(book)
        display = Display(rotation=config.DISPLAY_ROTATION,
                          inversion=config.DISPLAY_INVERSION, bgr=config.DISPLAY_BGR)
        dashboard = Dashboard(display)
        controller.start_thread()

        def status():
            result = controller.snapshot()
            result['ssid'] = config.WIFI_SSID
            result['ip'] = config.WIFI_IP
            if not result.get('running'):
                result['recipe_name'] = book.data['selected']
            else:
                result['recipe_name'] = result.get('recipe_name') or book.data['selected']
            return result

        dashboard.update(status())
        ap = start_access_point()
        portal = Portal(lambda: book.data, controller.save_config, status)
        dashboard.set_yield_hook(portal.poll)
        print('GOTHAM_READY SSID=%s IP=%s CHANNELS=%s PWM=PIO10kHz CONTROL=CORE1/50Hz' %
              (config.WIFI_SSID, config.WIFI_IP, config.ENABLED_CHANNELS))
        began = ticks_ms()
        last_display = began - 200
        last_report = began - 1000
        while duration_ms is None or ticks_diff(ticks_ms(), began) < duration_ms:
            now = ticks_ms()
            if controller.finished:
                raise RuntimeError(controller.error or 'Control worker exited unexpectedly')
            if ticks_diff(now, controller.heartbeat) > 500:
                controller.request_shutdown('Control worker missed its heartbeat')
                raise RuntimeError('Control worker missed its heartbeat')
            portal.poll()
            if ticks_diff(now, last_display) >= 200:
                dashboard.update(status())
                last_display = now
            if ticks_diff(now, last_report) >= 1000:
                value = status()
                fans = ' '.join('#%d:%s/%g%%' % (i,
                    str(round(f['rpm'])) if f['valid'] and f['rpm'] is not None else '--',
                    f['duty']) for i, f in enumerate(value['fans']) if f['enabled'])
                print('SPIN state=%s step=%s target=%.1f %s lag=%sms message=%s' %
                      (value['state'], value['step'], value['target_rpm'], fans,
                       value.get('loop_lag_ms', 0), value.get('message', '')))
                last_report = now
            sleep_ms(2)
    except KeyboardInterrupt:
        print('Gotham Spinner interrupted; outputs stopped.')
    except Exception as error:
        sys.print_exception(error)
        raise
    finally:
        try:
            if controller is not None:
                controller.close()
        finally:
            if portal is not None:
                portal.close()
            if ap is not None:
                ap.active(False)
        gc.collect()


if __name__ == '__main__':
    run()
