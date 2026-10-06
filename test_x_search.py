import io
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from search_backend import RequestScope, SearchError, Settings, completed_response, search_request, search_result
from x_search_mcp import RpcError, Server


def response(text="found", posts=None, calls=1, citations=None):
    answer = {"text": text, "posts": posts or []}
    return {"status": "completed", "model": "grok-test", "usage": {"server_side_tool_usage_details": {
        "x_search_calls": calls, "x_posts_fetched": len(posts or []), "x_users_fetched": 0}},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer),
            "annotations": [{"type": "url_citation", "url": url} for url in citations or []]}]}]}


def sse(value):
    return b'data: ' + json.dumps({"type": "response.completed", "response": value}).encode() + b'\n\n'


class BackendTests(unittest.TestCase):
    def test_filters_and_normalized_cache_identity(self):
        settings = Settings()
        args = {"query": " from:test ", "allowed_x_handles": ["@TEST", "test"],
                "from_date": "2026-10-01", "to_date": "2026-10-06"}
        req = search_request(args, settings)
        self.assertEqual(req["tools"], [{"type": "x_search", "allowed_x_handles": ["test"],
                                         "from_date": "2026-10-01", "to_date": "2026-10-06"}])
        self.assertEqual(req["max_tool_calls"], 5)
        self.assertEqual(req["max_output_tokens"], 4096)
        self.assertEqual(req["text"]["format"]["type"], "json_schema")
        self.assertEqual(req, search_request({**args, "cache_mode": "refresh"}, settings))

    def test_invalid_arguments(self):
        for args in ({"query": ""}, {"query": "a", "max_results": True}, {"query": "a", "extra": 1},
                     {"query": "a", "allowed_x_handles": None}, {"query": "a", "allowed_x_handles": ["../auth"]},
                     {"query": "a", "from_date": "2026-02-30"}, {"query": "a", "cache_mode": "forever"},
                     {"query": "a", "from_date": "2026-10-06", "to_date": "2026-10-01"}):
            with self.subTest(args=args), self.assertRaises(SearchError) as caught:
                search_request(args, Settings())
            self.assertEqual(caught.exception.code, "invalid_arguments")

    def test_configuration(self):
        settings = Settings.from_env({"X_SEARCH_MAX_TOOL_CALLS": "8", "X_SEARCH_MAX_OUTPUT_TOKENS": "6000",
                                      "X_SEARCH_TIMEOUT_SEC": "90", "X_SEARCH_CACHE_TTL_SEC": "0"})
        self.assertEqual((settings.max_tool_calls, settings.max_output_tokens, settings.timeout), (8, 6000, 90))
        for env in ({"X_SEARCH_TIMEOUT_SEC": "nan"}, {"X_SEARCH_WORKERS": "0"}, {"X_SEARCH_MAX_TOOL_CALLS": "1.5"},
                    {"X_SEARCH_MAGPIE_URL": "http://example.com/responses"},
                    {"X_SEARCH_MAGPIE_URL": "http://secret@127.0.0.1/responses"}):
            with self.subTest(env=env), self.assertRaises(SearchError):
                Settings.from_env(env)

    def test_structured_posts_require_matching_native_citations(self):
        post = {"url": "https://x.com/example/status/123", "text": "hello", "author": "example",
                "created_at": "2026-10-06T00:00:00Z", "kind": "original"}
        result = search_result(response(posts=[post, post], citations=["https://x.com/i/status/123"]))
        self.assertEqual(result["posts"], [post])
        self.assertEqual(result["search_usage"]["x_search_calls"], 1)
        with self.assertRaises(SearchError) as caught:
            search_result(response(posts=[post], citations=["https://x.com/i/status/999"]))
        self.assertEqual(caught.exception.code, "uncited_post")
        with self.assertRaises(SearchError):
            search_result(response(posts=[{**post, "kind": "imagined"}], citations=[post["url"]]))

    def test_malformed_upstream_and_no_search(self):
        values = [response(calls=0), {**response(), "usage": None}, {**response(), "output": None},
                  {**response(), "output": [{"type": "message", "content": None}]}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(SearchError):
                search_result(value)
        value = response()
        value["output"][0]["content"][0]["text"] = "not JSON"
        with self.assertRaises(SearchError):
            search_result(value)

    def test_root_citations_are_supported(self):
        post = {"url": "https://x.com/example/status/123", "text": "hello", "author": None,
                "created_at": None, "kind": "unknown"}
        value = response(posts=[post])
        value["citations"] = ["https://x.com/i/status/123"]
        self.assertEqual(search_result(value)["posts"], [post])

    def test_sse_frames(self):
        value = response()
        raw = json.dumps({"type": "response.completed", "response": value})
        split = raw.index('"response"')
        stream = io.BytesIO(b': heartbeat\r\n\r\ndata: {"type":"response.output_text.delta","delta":"ignored"}\r\n\r\n'
                            + b'data:' + raw[:split].encode() + b'\r\ndata: ' + raw[split:].encode() + b'\r\n\r\n')
        self.assertEqual(completed_response(stream), value)
        for raw in (b'data: [DONE]\n\n', b'data: nope\n\n', b'data: []\n\n',
                    b'data: {"type":"response.failed"}\n\n', b'data: {"type":"response.incomplete"}\n\n'):
            with self.subTest(raw=raw), self.assertRaises(SearchError):
                completed_response(io.BytesIO(raw))


class FakeService:
    def __init__(self, timeout=1):
        self.settings = SimpleNamespace(workers=2, timeout=timeout)
        self.started = threading.Event()
        self.finished = threading.Event()

    def search(self, args, scope):
        self.started.set()
        try:
            if args.get("query") == "unexpected":
                raise AttributeError("private upstream value")
            if args.get("query") == "wait":
                while True:
                    scope.wait(0.02)
            return {"text": "ok"}
        finally:
            self.finished.set()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.service = FakeService()
        self.server = Server(self.service, self.output)
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tearDown(self):
        self.server.close()

    def replies(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def call(self, request_id, query):
        self.server.handle({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                            "params": {"name": "x_search", "arguments": {"query": query}}})

    def wait_finished(self):
        deadline = time.monotonic() + 2
        while self.server.jobs and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.server.jobs)

    def test_ping_and_cancellation_while_search_is_running(self):
        self.call(2, "wait")
        self.assertTrue(self.service.started.wait(1))
        self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(self.replies()[-1], {"jsonrpc": "2.0", "id": 3, "result": {}})
        self.server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}})
        self.wait_finished()
        self.assertFalse(any(reply["id"] == 2 for reply in self.replies()))

    def test_total_timeout_returns_tool_error(self):
        self.service.settings.timeout = 0.1
        self.call(2, "wait")
        self.wait_finished()
        result = self.replies()[-1]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], "timeout")

    def test_queued_request_times_out_before_worker_is_available(self):
        output, service = io.StringIO(), FakeService(timeout=1)
        service.settings.workers = 1
        server = Server(service, output)
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        try:
            server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "x_search", "arguments": {"query": "wait"}}})
            self.assertTrue(service.started.wait(1))
            service.settings.timeout = 0.1
            server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "x_search", "arguments": {"query": "ok"}}})
            deadline = time.monotonic()+0.5
            while 3 in server.jobs and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertNotIn(3, server.jobs)
            self.assertIn(2, server.jobs)
            reply = [json.loads(line) for line in output.getvalue().splitlines() if json.loads(line)["id"] == 3][0]
            self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["error"]["code"], "timeout")
        finally:
            server.close()

    def test_unexpected_worker_failure_does_not_stop_server(self):
        self.call(2, "unexpected")
        self.wait_finished()
        result = self.replies()[-1]["result"]
        self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], "internal")
        self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(self.replies()[-1]["result"], {})

    def test_success_is_a_valid_mcp_tool_result(self):
        self.call(2, "ok")
        self.wait_finished()
        result = self.replies()[-1]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], {"text": "ok"})
        self.assertEqual(json.loads(result["content"][0]["text"]), {"text": "ok"})

    def test_correct_rpc_errors(self):
        for request, code in (({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": None}, -32602),
                              ({"jsonrpc": "2.0", "id": 2, "method": "unknown"}, -32601),
                              ({"jsonrpc": "1.0", "id": 2, "method": "ping"}, -32600)):
            with self.subTest(request=request), self.assertRaises(RpcError) as caught:
                self.server.handle(request)
            self.assertEqual(caught.exception.code, code)

    def test_stdio_recovers_after_parse_and_params_errors(self):
        lines = ['{', '[]', json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}),
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": None}),
                 json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                 json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})]
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('x_search_mcp.py'))],
                                input='\n'.join(lines)+'\n', text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["error"]["code"] for r in replies[:3]], [-32700, -32600, -32602])
        self.assertEqual(replies[-1]["result"]["tools"][0]["name"], "x_search")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
