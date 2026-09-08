"""Localhost-only browser preview. Does not import or access board hardware."""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'device'))
from recipes import DEFAULT_DATA, validate_document
from portal import Portal

data = json.loads(json.dumps(DEFAULT_DATA))
control = ROOT / 'artifacts' / 'portal-preview-control.json'
control.write_text('{"running": false}')


def status():
    running = json.loads(control.read_text()).get('running', False)
    return {'running': running, 'state': 'DWELL' if running else 'IDLE',
            'ip': '192.168.4.1', 'ssid': 'Gotham Spinner', 'target_rpm': 3000 if running else 0,
            'step': 2, 'step_count': 3, 'phase_remaining_s': 28.7,
            'message': 'Local browser preview; no hardware connected',
            'fans': [{'enabled': i < 4, 'rpm': 2995 + i, 'valid': running,
                      'duty': 80 if running else 0, 'error': 5 - i, 'fault': None}
                     for i in range(6)]}


def save(value):
    global data
    if status()['running']:
        raise RuntimeError('Run active')
    data = validate_document(value)
    (ROOT / 'artifacts' / 'portal-preview-saved.json').write_text(json.dumps(data, indent=2))


if __name__ == '__main__':
    app = Portal(lambda: data, save, status, host='127.0.0.1', port=18989, dns_port=18990)
    print('Portal preview at http://127.0.0.1:18989/', flush=True)
    try:
        while True:
            app.poll()
            time.sleep(0.002)
    finally:
        app.close()
