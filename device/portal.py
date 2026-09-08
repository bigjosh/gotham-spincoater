"""Bounded, nonblocking HTTP configuration and captive DNS for the Pico AP.

The caller owns Wi-Fi, motor control and persistent validation. poll() accepts
at most one client, handles one DNS packet and one small IO chunk per client.
There is deliberately no network endpoint that starts a motor.
"""
import socket
import json
try:
    from time import ticks_ms, ticks_diff
except ImportError:
    from time import monotonic
    def ticks_ms():
        return int(monotonic() * 1000)
    def ticks_diff(a, b):
        return a - b
from webpage import PAGE

MAX_BODY = 32768
MAX_HEADERS = 4096
IO_CHUNK = 1024
# This measures time without socket progress, including main-loop scheduling.
# Leave headroom for a display redraw; Dashboard also yields between fields.
CLIENT_TIMEOUT_MS = 5000


def _blocked(error):
    return bool(error.args) and error.args[0] in (11, 35, 10035)


def dns_answer(packet, ip='192.168.4.1'):
    """Answer one ordinary IN/A question; other record types get no answers."""
    if len(packet) < 12 or packet[2] & 0xf8 or packet[4:6] != b'\x00\x01':
        return None
    end = 12
    while end < len(packet):
        length = packet[end]
        end += 1
        if length == 0:
            break
        if length > 63 or end + length > len(packet) or end + length > 267:
            return None
        end += length
    else:
        return None
    if end + 4 > len(packet):
        return None
    question = packet[12:end + 4]
    answer_a = packet[end:end + 4] == b'\x00\x01\x00\x01'
    flags = bytes((0x84 | (packet[2] & 1), 0))
    header = packet[:2] + flags + b'\x00\x01' + (
        b'\x00\x01' if answer_a else b'\x00\x00') + b'\x00\x00\x00\x00'
    if not answer_a:
        return header + question
    address = bytes(int(part) for part in ip.split('.'))
    if len(address) != 4:
        return None
    return header + question + b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x1e\x00\x04' + address


class Portal:
    def __init__(self, get_config, set_config, get_status,
                 host='0.0.0.0', port=80, dns_port=53):
        self.get_config, self.set_config, self.get_status = get_config, set_config, get_status
        self._clients = []
        self._server = None
        self._dns = None
        self._closed = False
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except OSError:
                pass
            self._server.setblocking(False)
            self._server.bind((host, port))
            self._server.listen(2)
            self.port = self._server.getsockname()[1] if hasattr(self._server, 'getsockname') else port
            self._dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._dns.setblocking(False)
            self._dns.bind((host, dns_port))
            self.dns_port = self._dns.getsockname()[1] if hasattr(self._dns, 'getsockname') else dns_port
        except BaseException:
            self.close()
            raise

    def _ip(self):
        return self.get_status().get('ip') or '192.168.4.1'

    def poll(self):
        if self._closed:
            return
        try:
            packet, address = self._dns.recvfrom(512)
            reply = dns_answer(packet, self._ip())
            if reply is not None:
                self._dns.sendto(reply, address)
        except (OSError, ValueError):
            pass
        if len(self._clients) < 2:
            try:
                client, _ = self._server.accept()
                client.setblocking(False)
                self._clients.append({'socket': client, 'rx': bytearray(),
                                      'last': ticks_ms(), 'tx': None, 'sent': 0,
                                      'header_end': None, 'length': None})
            except OSError as error:
                if not _blocked(error):
                    raise
        for client in tuple(self._clients):
            if ticks_diff(ticks_ms(), client['last']) >= CLIENT_TIMEOUT_MS:
                self._drop(client)
                continue
            try:
                if client.get('draining', False):
                    # Discard queued excess input after sending an early error.
                    # Closing with unread TCP data can reset away that response.
                    try:
                        remainder = client['socket'].recv(IO_CHUNK)
                    except OSError as error:
                        if _blocked(error):
                            self._drop(client)
                            continue
                        raise
                    if not remainder:
                        self._drop(client)
                elif client['tx'] is None:
                    data = client['socket'].recv(IO_CHUNK)
                    if not data:
                        self._drop(client)
                        continue
                    client['last'] = ticks_ms()
                    client['rx'].extend(data)
                    self._request(client)
                else:
                    start = client['sent']
                    count = client['socket'].send(memoryview(client['tx'])[start:start + IO_CHUNK])
                    if count:
                        client['last'] = ticks_ms()
                        client['sent'] += count
                    if client['sent'] >= len(client['tx']):
                        client['draining'] = True
            except OSError as error:
                if not _blocked(error):
                    self._drop(client)
            except Exception:
                # Keep a malformed/disconnected browser from stopping control.
                if client in self._clients:
                    self._respond(client, 500, {'error': 'Request failed'})

    def _request(self, client):
        data = client['rx']
        if client['header_end'] is None:
            end = data.find(b'\r\n\r\n')
            if end < 0:
                if len(data) > MAX_HEADERS:
                    self._respond(client, 431, {'error': 'Request headers too large'})
                return
            if end > MAX_HEADERS:
                self._respond(client, 431, {'error': 'Request headers too large'})
                return
            try:
                lines = bytes(data[:end]).decode('utf-8').split('\r\n')
                method, path, version = lines[0].split(' ')
                if version not in ('HTTP/1.0', 'HTTP/1.1'):
                    raise ValueError('HTTP version')
                headers = {}
                for line in lines[1:]:
                    name, value = line.split(':', 1)
                    name = name.strip().lower()
                    if name in headers:
                        raise ValueError('Duplicate header')
                    headers[name] = value.strip()
                if 'transfer-encoding' in headers:
                    raise ValueError('Chunked requests are not supported')
                length = int(headers.get('content-length', '0'))
                if length < 0:
                    raise ValueError('Negative content length')
                client['method'], client['path'] = method, path.split('?', 1)[0]
                client['headers'] = headers
                client['header_end'], client['length'] = end + 4, length
            except (ValueError, UnicodeError):
                self._respond(client, 400, {'error': 'Malformed HTTP request'})
                return
            if length > MAX_BODY:
                self._respond(client, 413, {'error': 'Configuration is limited to 32 KiB'})
                return
        if len(data) >= client['header_end'] + client['length']:
            self._route(client)

    def _route(self, client):
        method, path = client['method'], client['path']
        if method == 'GET' and path == '/api/status':
            self._respond(client, 200, self.get_status())
        elif method == 'GET' and path == '/api/config':
            self._respond(client, 200, self.get_config())
        elif method in ('PUT', 'POST') and path == '/api/config':
            if self.get_status().get('running', False):
                self._respond(client, 409, {'error': 'Stop the run before changing recipes'})
                return
            if client['headers'].get('content-type', '').split(';')[0].strip() != 'application/json':
                self._respond(client, 415, {'error': 'Use application/json'})
                return
            start = client['header_end']
            try:
                value = json.loads(bytes(client['rx'][start:start + client['length']]).decode('utf-8'))
                if not isinstance(value, dict):
                    raise ValueError('Configuration must be a JSON object')
                self.set_config(value)
            except (ValueError, TypeError) as error:
                self._respond(client, 400, {'error': str(error)})
                return
            except RuntimeError as error:
                self._respond(client, 409, {'error': str(error)})
                return
            self._respond(client, 200, self.get_config())
        elif path.startswith('/api/'):
            self._respond(client, 404, {'error': 'No such endpoint'})
        elif method not in ('GET', 'HEAD'):
            self._respond(client, 405, {'error': 'Method not allowed'})
        elif path == '/favicon.ico':
            self._respond(client, 204, b'', 'image/x-icon')
        elif path == '/' and client['headers'].get('host', '').split(':')[0] in (
                '', self._ip(), '127.0.0.1', 'localhost'):
            self._respond(client, 200, b'' if method == 'HEAD' else PAGE,
                          'text/html; charset=utf-8')
        else:
            self._respond(client, 302, b'', extra='Location: http://%s/\r\n' % self._ip())

    def _respond(self, client, code, body, content_type='application/json', extra=''):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode('utf-8')
        reasons = {200: 'OK', 204: 'No Content', 302: 'Found', 400: 'Bad Request',
                   404: 'Not Found', 405: 'Method Not Allowed', 409: 'Conflict',
                   413: 'Payload Too Large', 415: 'Unsupported Media Type',
                   431: 'Request Header Fields Too Large',
                   500: 'Internal Server Error'}
        header = ('HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n'
                  'Connection: close\r\nCache-Control: no-store\r\n'
                  'X-Content-Type-Options: nosniff\r\n%s\r\n') % (
                      code, reasons[code], content_type, len(body), extra)
        client['tx'] = header.encode('utf-8') + body
        client['sent'] = 0
        client['rx'] = None

    def _drop(self, client):
        try:
            client['socket'].close()
        finally:
            if client in self._clients:
                self._clients.remove(client)

    def close(self):
        self._closed = True
        for client in tuple(self._clients):
            self._drop(client)
        for sock in (self._server, self._dns):
            if sock is not None:
                sock.close()
