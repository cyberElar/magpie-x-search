import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import x_search_mcp as m


class SearchTests(unittest.TestCase):
    def test_filters_and_dates(self):
        req = m.search_request({"query": "from:test", "allowed_x_handles": ["@test"],
                                "from_date": "2026-10-01", "to_date": "2026-10-06"})
        self.assertEqual(req["max_tool_calls"], 1)
        self.assertEqual(req["max_output_tokens"], 1600)
        self.assertEqual(req["tools"], [{"type": "x_search", "allowed_x_handles": ["test"],
                                         "from_date": "2026-10-01", "to_date": "2026-10-06"}])
        for args in ({"query": ""}, {"query": "a", "max_results": True},
                     {"query": "a", "allowed_x_handles": ["../auth"]},
                     {"query": "a", "from_date": "2026-02-30"},
                     {"query": "a", "from_date": "2026-10-06", "to_date": "2026-10-01"}):
            with self.assertRaises(ValueError):
                m.search_request(args)

    def test_no_search_cannot_be_reported_as_success(self):
        with self.assertRaises(RuntimeError):
            m.search_result({"output": [{"type": "message", "content": [{"type": "output_text", "text": "guess"}]}]})

    def test_sse_citations_and_execution_evidence(self):
        response = {"status": "completed", "usage": {"server_side_tool_usage_details": {"x_search_calls": 2}},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "found",
                        "annotations": [{"type": "url_citation", "url": "https://x.com/i/status/1"}]}]}]}
        stream = io.BytesIO(b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                            + b'data: ' + json.dumps({"type": "response.completed", "response": response}).encode() + b'\n\n')
        result = m.search_result(m.completed_response(stream))
        self.assertEqual(result["text"], "found")
        self.assertEqual(result["citations"], ["https://x.com/i/status/1"])
        self.assertEqual(result["search_usage"]["x_search_calls"], 2)
        with self.assertRaises(RuntimeError):
            m.completed_response(io.BytesIO(b'data: [DONE]\n'))

    def test_mcp_errors_and_stdio_handshake(self):
        with patch.object(m, "x_search", side_effect=RuntimeError("offline")):
            result = m.dispatch({"method": "tools/call", "params": {"name": "x_search", "arguments": {"query": "test"}}})
        self.assertTrue(result["isError"])
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        result = subprocess.run([sys.executable, str(Path(m.__file__))],
                                input="\n".join(json.dumps(r) for r in requests) + "\n", text=True,
                                capture_output=True, check=True)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "x_search")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
