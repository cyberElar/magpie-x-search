import http.server
import json
import socket
import threading
import time
import unittest

from search_backend import MagpieClient, RequestScope, SearchError, Settings, search_request
from test_x_search import response, sse


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.server.lock:
            self.server.requests.append(body)
        self.server.started.set()
        if self.path == "/limited":
            self.send_response(429)
            self.send_header('Retry-After', '12')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == "/stall-headers":
            try:
                self.connection.settimeout(2)
                if self.connection.recv(1) == b'':
                    self.server.disconnected.set()
            except (OSError, TimeoutError):
                pass
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        if self.path in ("/stall-body", "/slow-body"):
            try:
                if self.path == "/slow-body":
                    for _ in range(60):
                        self.wfile.write(b': progress\n\n')
                        self.wfile.flush()
                        if self.server.finish.wait(0.02):
                            return
                else:
                    self.connection.settimeout(2)
                    if self.connection.recv(1) == b'':
                        self.server.disconnected.set()
            except OSError:
                self.server.disconnected.set()
            return
        if self.path == "/broken":
            self.wfile.write(b'data: not-json\n\n')
        else:
            self.wfile.write(sse(response()))
        self.wfile.flush()


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        cls.http.requests, cls.http.lock = [], threading.Lock()
        cls.http.started, cls.http.disconnected, cls.http.finish = threading.Event(), threading.Event(), threading.Event()
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.finish.set()
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def setUp(self):
        self.http.started.clear()
        self.http.disconnected.clear()

    def client(self, path):
        return MagpieClient(Settings(endpoint=f'http://127.0.0.1:{self.http.server_port}/{path}'))

    def run_search(self, path, scope):
        client = self.client(path)
        return client.search(search_request({"query": "test"}, client.settings), scope)

    def test_real_http_sse_and_request_shape(self):
        scope = RequestScope(2)
        try:
            self.assertEqual(self.run_search('ok', scope)["text"], "found")
            self.assertEqual(self.http.requests[-1]["tools"], [{"type": "x_search"}])
            self.assertEqual(self.http.requests[-1]["text"]["format"]["type"], 'json_schema')
        finally:
            scope.close()

    def test_rate_limit_is_reported_without_retry(self):
        before = len(self.http.requests)
        scope = RequestScope(2)
        try:
            with self.assertRaises(SearchError) as caught:
                self.run_search('limited', scope)
            self.assertEqual(caught.exception.code, 'rate_limited')
            self.assertEqual(caught.exception.details['retry_after_sec'], 12)
            self.assertEqual(len(self.http.requests), before+1)
        finally:
            scope.close()

    def test_cancel_interrupts_blocked_body_read(self):
        scope, errors = RequestScope(3), []
        def request():
            try:
                self.run_search('stall-body', scope)
            except SearchError as exc:
                errors.append(exc.code)
        worker = threading.Thread(target=request)
        try:
            worker.start()
            self.assertTrue(self.http.started.wait(1))
            scope.stop()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, ['cancelled'])
            self.assertTrue(self.http.disconnected.wait(1))
        finally:
            scope.stop()
            scope.close()
            worker.join(timeout=2)

    def test_total_deadline_covers_headers_and_stream_with_progress(self):
        for path in ('stall-headers', 'slow-body'):
            with self.subTest(path=path):
                scope = RequestScope(0.15)
                started = time.monotonic()
                try:
                    with self.assertRaises(SearchError) as caught:
                        self.run_search(path, scope)
                    self.assertEqual(caught.exception.code, 'timeout')
                    self.assertLess(time.monotonic()-started, 1)
                finally:
                    scope.close()

    def test_bad_sse_is_a_structured_failure(self):
        scope = RequestScope(2)
        try:
            with self.assertRaises(SearchError) as caught:
                self.run_search('broken', scope)
            self.assertEqual(caught.exception.code, 'upstream_protocol')
        finally:
            scope.close()


if __name__ == '__main__':
    unittest.main()
