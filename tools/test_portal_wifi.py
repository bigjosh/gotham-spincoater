"""External host HTTP/DNS stress test; connect to the coater AP first.

Run alongside test_app_board.py's disposable recipe store. This sends a
same-document idle save, never a motor-start command or a recipe change.
"""
import http.client
import json
import socket
import time

HOST = '192.168.4.1'


def request(path, body=None):
    client = http.client.HTTPConnection(HOST, timeout=3)
    try:
        client.request('GET' if body is None else 'PUT', path,
                       body=None if body is None else json.dumps(body),
                       headers={'Content-Type': 'application/json'})
        response = client.getresponse()
        return response.status, response.read(), response.getheader('Location')
    finally:
        client.close()


for attempt in range(4):
    try:
        code, body, _ = request('/api/config')
        break
    except OSError:
        if attempt == 3:
            raise
        time.sleep(0.5)  # DHCP/reassociation after the board restarts its AP.
assert code == 200
document = json.loads(body)
assert request('/api/config', document)[0] == 200, 'Run during the20s idle window'
assert json.loads(request('/api/config')[1]) == document
print('HTTP configuration save/readback: OK', flush=True)
code, body, _ = request('/')
assert code == 200 and b'Gotham' in body
print('HTTP editor page:', len(body), 'bytes', flush=True)
assert request('/generate_204')[0] == 302

query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x04test\x03com\x00\x00\x01\x00\x01'
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns:
    dns.settimeout(3)
    dns.sendto(query, (HOST, 53))
    answer, _ = dns.recvfrom(512)
assert answer[:2] == query[:2] and answer[-4:] == bytes((192, 168, 4, 1))
print('Captive DNS and HTTP redirect: OK', flush=True)

started = time.monotonic()
locked = False
holds = []
hold_started = None
worst_lag = overflows = requests = 0
while time.monotonic() - started < 90:
    try:
        code, body, _ = request('/api/status')
    except (OSError, http.client.HTTPException):
        print('WIFI_FAILURE elapsed=%.1fs requests=%d hold_samples=%d last_state=%s' %
              (time.monotonic() - started, requests, len(holds),
               value['state'] if 'value' in globals() else 'none'), flush=True)
        raise
    assert code == 200
    value = json.loads(body)
    assert value['state'] not in ('FAULT', 'STOPPED'), value
    fan = value['fans'][0]
    worst_lag = max(worst_lag, value.get('loop_lag_ms', 0))
    overflows = max(overflows, fan.get('tach_overflows', 0))
    if value['running'] and not locked:
        assert request('/api/config', document)[0] == 409
        locked = True
        print('Running recipe edit lockout: OK', flush=True)
    if value['state'] == 'DWELL' and value['target_rpm'] == 3000:
        if hold_started is None:
            hold_started = time.monotonic()
        if fan['valid']:
            if 2950 <= fan['rpm'] <= 3050:
                holds.append(fan['rpm'])
            else:
                assert 'paused' in value['message'].lower(), value
        # Finish while the board's30s dwell is still active. The board test
        # switches its AP off after completing the recipe, closing clients.
        if time.monotonic() - hold_started >= 20:
            break
    assert request('/')[0] == 200
    requests += 2
    time.sleep(0.2)
assert locked and holds, 'Did not observe run and3000RPM hold'
assert all(2950 <= rpm <= 3050 for rpm in holds), (min(holds), max(holds))
print('WIFI_PORTAL_PASS requests=%d hold_samples=%d rpm_range=%.1f..%.1f worst_lag=%dms overflows=%d' %
      (requests, len(holds), min(holds), max(holds), worst_lag, overflows), flush=True)
