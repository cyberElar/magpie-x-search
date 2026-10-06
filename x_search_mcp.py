#!/usr/bin/env python3
"""Concurrent MCP stdio server exposing native X search through local magpie."""
import concurrent.futures
import json
import sys
import threading

from search_backend import MagpieClient, RequestScope, SearchError, Settings
from search_cache import SearchService

VERSION = "1.1.0"
PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
TOOL = {
    "name": "x_search",
    "description": (
        "Search public X/Twitter profiles, posts and threads using native xAI X search through local magpie OAuth. "
        "Returns a summary, structured posts, native source URLs and execution counts. "
        "Results are retrieved samples, not a complete archive. Use cache_mode=refresh for a fresh lookup."
    ),
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
    "inputSchema": {
        "type": "object", "additionalProperties": False, "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 8000},
            "allowed_x_handles": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string"},
                                  "description": "Optional author filter; leading @ is accepted."},
            "from_date": {"type": "string", "description": "Optional start date, YYYY-MM-DD."},
            "to_date": {"type": "string", "description": "Optional end date, YYYY-MM-DD."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            "cache_mode": {"type": "string", "enum": ["use", "refresh", "bypass"], "default": "use",
                           "description": "use: shared cached results; refresh: fresh coalesced search; bypass: no cache or coalescing."},
        },
    },
}


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def tool_result(value=None, error=None):
    if error:
        value = {"error": {"code": error.code, "message": str(error), **error.details}}
    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error is not None}
    if error is None:
        result["structuredContent"] = value
    return result


def validate_request(request):
    if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
            or not isinstance(request.get("method"), str)):
        raise RpcError(-32600, "Invalid JSON-RPC request")
    if "id" in request and type(request["id"]) not in (str, int):
        raise RpcError(-32600, "Request id must be a string or integer")
    if not isinstance(request.get("params", {}), dict):
        raise RpcError(-32602, "params must be an object")


class Server:
    def __init__(self, service, output=None):
        self.service = service
        self.output = output or sys.stdout
        self.lock = threading.RLock()
        self.jobs = {}
        self.initialized = False
        self.ready = False
        self.closed = False
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=service.settings.workers)

    def send(self, request_id, result=None, error=None):
        reply = {"jsonrpc": "2.0", "id": request_id}
        reply["error" if error else "result"] = error or result
        with self.lock:
            if not self.closed:
                self.output.write(json.dumps(reply, ensure_ascii=False) + "\n")
                self.output.flush()

    def finish(self, request_id, scope, future):
        try:
            value = future.result()
            scope.check()
            result = tool_result(value)
        except SearchError as exc:
            result = tool_result(error=exc)
        except concurrent.futures.CancelledError:
            result = tool_result(error=SearchError(scope.reason or "cancelled", "Search stopped before execution"))
        except Exception:
            print("magpie-x-search: unexpected search failure", file=sys.stderr)
            result = tool_result(error=SearchError("internal", "Unexpected search failure; inspect local server logs"))
        with self.lock:
            self.jobs.pop(request_id, None)
            # MCP cancellation notifications do not receive a tool response.
            if scope.reason != "cancelled" and result is not None:
                self.send(request_id, result=result)
        scope.close()

    def call_tool(self, request_id, params):
        if params.get("name") != "x_search" or not isinstance(params.get("arguments", {}), dict):
            raise RpcError(-32602, "Expected x_search with an arguments object")
        with self.lock:
            if request_id in self.jobs:
                raise RpcError(-32600, "Request id is already in use")
            if len(self.jobs) >= self.service.settings.workers * 2:
                self.send(request_id, result=tool_result(error=SearchError("busy", "Too many concurrent searches; call again later")))
                return
            scope = RequestScope(self.service.settings.timeout)
            future = self.executor.submit(self.service.search, params.get("arguments", {}), scope)
            self.jobs[request_id] = (scope, future)
            scope.attach(future.cancel)
            future.add_done_callback(lambda done: self.finish(request_id, scope, done))

    def notification(self, method, params):
        if method == "notifications/initialized" and self.initialized:
            self.ready = True
        elif method == "notifications/cancelled":
            request_id = params.get("requestId")
            if type(request_id) not in (str, int):
                return
            with self.lock:
                job = self.jobs.get(request_id)
                if job:
                    job[0].stop()
                    job[1].cancel()

    def handle(self, request):
        validate_request(request)
        method, params = request["method"], request.get("params", {})
        if "id" not in request:
            self.notification(method, params)
            return
        request_id = request["id"]
        if method == "initialize":
            if self.initialized or not isinstance(params.get("protocolVersion"), str):
                raise RpcError(-32602, "Invalid initialization")
            version = params["protocolVersion"]
            self.send(request_id, result={"protocolVersion": version if version in PROTOCOLS else PROTOCOLS[-1],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "magpie-x-search", "version": VERSION}})
            self.initialized = True
        elif method == "ping":
            self.send(request_id, result={})
        elif not self.ready:
            raise RpcError(-32000, "Complete MCP initialization first")
        elif method == "tools/list":
            self.send(request_id, result={"tools": [TOOL]})
        elif method == "tools/call":
            self.call_tool(request_id, params)
        else:
            raise RpcError(-32601, "Method not found")

    def run(self, source):
        try:
            for line in source:
                request = None
                try:
                    request = json.loads(line)
                except ValueError:
                    self.send(None, error={"code": -32700, "message": "Parse error"})
                    continue
                try:
                    self.handle(request)
                except RpcError as exc:
                    # Invalid cancellation notifications are fire-and-forget.
                    if (isinstance(request, dict) and "id" not in request
                            and isinstance(request.get("method"), str) and request["method"].startswith("notifications/")):
                        continue
                    request_id = request.get("id") if isinstance(request, dict) else None
                    if type(request_id) not in (str, int):
                        request_id = None
                    self.send(request_id, error={"code": exc.code, "message": str(exc)})
        finally:
            self.close()

    def close(self):
        with self.lock:
            self.closed = True
            for scope, future in list(self.jobs.values()):
                scope.stop()
                future.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)


def main():
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        settings = Settings.from_env()
        Server(SearchService(settings, MagpieClient(settings))).run(sys.stdin)
    except SearchError as exc:
        print(f"magpie-x-search: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
