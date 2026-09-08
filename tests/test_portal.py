"""Local sockets only: protocol limits, captive DNS, idle-only config writes."""
import importlib.util
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

DEVICE = Path(__file__).resolve().parents[1] / 'device'
sys.path.insert(0, str(DEVICE))
from portal import Portal, dns_answer
import portal


class PortalTests(unittest.TestCase):
    def test_socket_subset_without_getsockname(self):
        class MinimalSocket:
            def setsockopt(self, *args): pass
            def setblocking(self, value): pass
            def bind(self, address): self.address = address
            def listen(self, count): pass
            def close(self): self.closed = True
        sockets = []
        def make_socket(*args):
            sock = MinimalSocket()
            sockets.append(sock)
            return sock
        with patch.object(portal.socket, 'socket', side_effect=make_socket):
            app = Portal(lambda: {}, lambda value: None, lambda: {})
            self.assertEqual(app.port, 80)
            self.assertEqual(app.dns_port, 53)
            app.close()
        self.assertTrue(all(sock.closed for sock in sockets))

    def setUp(self):
        self.config = {'version': 1, 'selected': 'Default', 'settings': {}, 'profiles': []}
        self.running = False
        self.saved = 0
        self.portal = Portal(lambda: self.config, self.save,
                             lambda: {'running': self.running, 'state': 'IDLE',
                                      'ip': '192.168.4.1', 'fans': []},
                             host='127.0.0.1', port=0, dns_port=0)

    def tearDown(self):
        self.portal.close()

    def save(self, config):
        if config.get('version') != 1:
            raise ValueError('Unsupported version')
        self.saved += 1
        self.config = config

    def connect(self):
        sock = socket.socket()
        sock.settimeout(1)
        sock.connect(('127.0.0.1', self.portal.port))
        return sock

    def request(self, method='GET', path='/api/config', body=b'', extra=''):
        sock = self.connect()
        message = ('%s %s HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                   'Content-Length: %d\r\n%s\r\n') % (method, path, len(body), extra)
        sock.sendall(message.encode() + body)
        sock.setblocking(False)
        result = bytearray()
        try:
            for _ in range(5000):
                self.portal.poll()
                try:
                    data = sock.recv(65536)
                    if not data:
                        break
                    result.extend(data)
                except BlockingIOError:
                    pass
                except ConnectionResetError:
                    # Windows may report RST after the complete early 413
                    # response when an oversized body remains unread.
                    if result:
                        break
                    raise
            else:
                self.fail('Request did not finish within bounded polls')
        finally:
            sock.close()
        header, payload = bytes(result).split(b'\r\n\r\n', 1)
        return int(header.split(b' ')[1]), header, payload

    def test_configuration_and_status_get(self):
        code, _, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), self.config)
        code, _, body = self.request(path='/api/status')
        self.assertFalse(json.loads(body)['running'])

    def test_idle_save_uses_callback_and_returns_canonical_configuration(self):
        value = {'version': 1, 'selected': 'Updated'}
        code, _, body = self.request('PUT', body=json.dumps(value).encode(),
                                    extra='Content-Type: application/json\r\n')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), value)
        self.assertEqual(self.saved, 1)

    def test_running_write_is_rejected_without_saving(self):
        self.running = True
        code, _, _ = self.request('POST', body=b'{"version":1}',
                                  extra='Content-Type: application/json\r\n')
        self.assertEqual(code, 409)
        self.assertEqual(self.saved, 0)

    def test_validation_errors_and_non_json_types(self):
        for body in (b'{broken', b'[]', b'{"version":2}'):
            code, _, _ = self.request('PUT', body=body,
                                      extra='Content-Type: application/json\r\n')
            self.assertEqual(code, 400)
        code, _, _ = self.request('PUT', body=b'{"version":1}')
        self.assertEqual(code, 415)
        self.assertEqual(self.saved, 0)

    def test_body_limit_rejects_before_saving(self):
        code, _, _ = self.request('PUT', body=b' ' * 32769,
                                  extra='Content-Type: application/json\r\n')
        self.assertEqual(code, 413)
        self.assertEqual(self.saved, 0)

    def test_page_is_self_contained_and_has_no_remote_start(self):
        code, _, body = self.request(path='/')
        self.assertEqual(code, 200)
        self.assertIn(b'Save to coater', body)
        self.assertNotIn(b'<script src=', body)
        self.assertNotIn(b'/api/start', body)
        self.assertEqual(self.request('POST', '/api/start')[0], 404)

    def test_captive_os_probes_redirect_to_access_point(self):
        for path in ('/generate_204', '/hotspot-detect.html', '/connecttest.txt',
                     '/ncsi.txt', '/library/test/success.html'):
            code, header, _ = self.request(path=path)
            self.assertEqual(code, 302)
            self.assertIn(b'Location: http://192.168.4.1/', header)

    def test_slow_partial_client_does_not_block_second_client(self):
        slow = self.connect()
        try:
            slow.sendall(b'GET / HTTP/1.1\r\nHost:')
            self.portal.poll()
            self.assertEqual(self.request(path='/api/status')[0], 200)
            self.assertEqual(len(self.portal._clients), 1)
            last = self.portal._clients[0]['last']
            with patch.object(portal, 'ticks_ms', return_value=last + 4999):
                self.portal.poll()
            self.assertEqual(len(self.portal._clients), 1)
            with patch.object(portal, 'ticks_ms', return_value=last + 5000):
                self.portal.poll()
            self.assertEqual(len(self.portal._clients), 0)
        finally:
            slow.close()

    def test_response_completes_after_display_stall_between_chunks(self):
        self.config['payload'] = 'x' * 2000
        sock = self.connect()
        try:
            sock.sendall(b'GET /api/config HTTP/1.1\r\nHost: localhost\r\n\r\n')
            with patch.object(portal, 'ticks_ms', return_value=1000):
                self.portal.poll()  # Accept and parse the request.
                self.portal.poll()  # Send exactly the first IO_CHUNK.
            first = sock.recv(65536)
            self.assertEqual(len(first), portal.IO_CHUNK)
            response = bytearray(first)
            sock.setblocking(False)
            # The observed hardware redraw took 2.4 s, exceeding the old
            # deadline and truncating the response immediately on resumption.
            with patch.object(portal, 'ticks_ms', return_value=3400):
                for _ in range(10):
                    self.portal.poll()
                    try:
                        part = sock.recv(65536)
                    except BlockingIOError:
                        continue
                    if not part:
                        break
                    response.extend(part)
            header, body = bytes(response).split(b'\r\n\r\n', 1)
            length = int(next(line.split(b':', 1)[1] for line in header.split(b'\r\n')
                              if line.lower().startswith(b'content-length:')))
            self.assertEqual(len(body), length)
            self.assertEqual(json.loads(body), self.config)
        finally:
            sock.close()

    def test_chunked_requests_rejected(self):
        self.assertEqual(self.request('PUT', extra='Transfer-Encoding: chunked\r\n')[0], 400)

    def test_dns_answers_a_but_not_aaaa(self):
        packet = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x03www\x07example\x03com\x00'
        response = dns_answer(packet + b'\x00\x01\x00\x01')
        self.assertEqual(response[:2], b'\x12\x34')
        self.assertEqual(response[6:8], b'\x00\x01')
        self.assertEqual(response[-4:], bytes((192, 168, 4, 1)))
        response = dns_answer(packet + b'\x00\x1c\x00\x01')
        self.assertEqual(response[6:8], b'\x00\x00')
        self.assertIsNone(dns_answer(b'bad'))
        self.assertIsNone(dns_answer(packet[:12] + b'\xc0\x0c\x00\x01\x00\x01'))


class DisplayStub:
    def __init__(self):
        self.calls = []

    def fill(self, color):
        self.calls.append(('fill', color))

    def fill_rect(self, x, y, width, height, color):
        assert x >= 0 and y >= 0 and x + width <= 480 and y + height <= 320
        self.calls.append(('rect', x, y, width, height, color))

    def hline(self, x, y, width, color):
        self.fill_rect(x, y, width, 1, color)

    def text(self, text, x, y, color, scale=1, bg=None):
        assert x >= 0 and y >= 0 and x + len(str(text)) * 8 * scale <= 480
        assert y + 8 * scale <= 320
        self.calls.append(('text', text, x, y))


class DashboardTests(unittest.TestCase):
    def test_yields_between_small_draw_batches_and_preserves_cache(self):
        from dashboard import Dashboard
        screen = DisplayStub()
        dashboard = Dashboard(screen)
        previous = len(screen.calls)
        batches = []

        def service():
            nonlocal previous
            batches.append(len(screen.calls) - previous)
            previous = len(screen.calls)

        dashboard.set_yield_hook(service)
        snapshot = {'fans': [{'enabled': True, 'valid': True, 'rpm': 3000,
                              'duty': 80} for _ in range(6)]}
        dashboard.update(snapshot)
        self.assertGreater(len(batches), 20)
        self.assertTrue(all(1 <= size <= 2 for size in batches))
        self.assertEqual(previous, len(screen.calls))
        count = len(batches)
        dashboard.update(snapshot)
        self.assertEqual(len(batches), count)
        dashboard.set_yield_hook(None)
        snapshot['fans'][0]['rpm'] = 3001
        dashboard.update(snapshot)
        self.assertEqual(len(batches), count)

    def test_numeric_tracking_error_is_not_a_fault(self):
        from dashboard import Dashboard
        screen = DisplayStub()
        dashboard = Dashboard(screen)
        snapshot = {'state': 'DWELL', 'fans': [
            {'enabled': True, 'valid': True, 'rpm': 2995, 'duty': 70, 'error': 5}]}
        dashboard.update(snapshot)
        self.assertFalse(any(c[0] == 'text' and c[1] == 'FAULT' for c in screen.calls))
        snapshot['fans'][0]['fault'] = 'Tach signal missing'
        dashboard.update(snapshot)
        self.assertTrue(any(c[0] == 'text' and c[1] == 'FAULT' for c in screen.calls))

    def test_all_six_tiles_and_unchanged_snapshot_uses_no_display_io(self):
        from dashboard import Dashboard
        screen = DisplayStub()
        dashboard = Dashboard(screen)
        snapshot = {'state': 'DWELL', 'running': True, 'target_rpm': 3000,
                    'step': 2, 'step_count': 3, 'phase_remaining_s': 28.7,
                    'recipe_name': 'Default', 'ssid': 'Gotham', 'ip': '192.168.4.1',
                    'fans': [{'enabled': i < 4, 'valid': True, 'rpm': 3000 + i,
                              'duty': 80, 'error': None} for i in range(6)]}
        dashboard.update(snapshot)
        self.assertTrue(all(any(call[0] == 'text' and call[1] == 'FAN #%d' % i
                                for call in screen.calls) for i in range(6)))
        count = len(screen.calls)
        dashboard.update(snapshot)
        self.assertEqual(len(screen.calls), count)
        snapshot['fans'][0]['rpm'] = 3010
        dashboard.update(snapshot)
        self.assertLessEqual(len(screen.calls) - count, 2)


if __name__ == '__main__':
    unittest.main()
