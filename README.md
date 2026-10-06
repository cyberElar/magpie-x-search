# magpie-x-search

An MCP `x_search` tool lets any calling model search public X content through
Grok's native search. The server uses an existing local magpie OAuth login.
GPT, Claude, DeepSeek, and other MCP clients receive the result through MCP.

The server returns a summary, structured posts, source URLs, and search counts.
The server also shares cached results and concurrent searches across local terminals.

Licensed under the [MIT License](LICENSE).

## Setup

Requires Python 3.10 or later, a running [magpie](https://usemagpie.ai), and a
signed-in Grok provider. The default model is `grok-plugin/grok-4.7`.
No Python packages are required.

1. Sign in to Grok in magpie through `@magpie-community/opencode-grok-auth`.
   The plugin supplies OAuth credentials, token refresh, and the upstream proxy.
2. Clone the repository into a new folder:

   ```sh
   gh repo clone cyberElar/magpie-x-search
   cd magpie-x-search
   ```

3. Register the server from the repository root:

   ```sh
   codex mcp add x-search -- python3 "$PWD/x_search_mcp.py"
   ```

4. Set `tool_timeout_sec = 180` in the `[mcp_servers.x-search]` section of
   Codex's `config.toml`. The server's default total timeout is 150 seconds.
5. Start a new Codex session so Codex discovers the server.

For other MCP clients, replace the path in this configuration with your clone path:

```json
{
  "mcpServers": {
    "x-search": {
      "command": "python3",
      "args": ["/absolute/path/to/magpie-x-search/x_search_mcp.py"]
    }
  }
}
```

On Windows, use the installed Python command and an absolute Windows path.
Each machine needs its own magpie login. Terminals on one machine share the
local magpie gateway and the default cache database.

To update an existing clone, run `git pull` and restart the MCP client.
To remove the Codex registration, run `codex mcp remove x-search`.

## Tool

```json
{
  "query": "from:example Find three recent public posts. Include dates and source links.",
  "allowed_x_handles": ["example"],
  "from_date": "2026-10-01",
  "to_date": "2026-10-06",
  "max_results": 3,
  "cache_mode": "use"
}
```

Only `query` is required. Handle filters accept 1 to 20 handles.
Dates use `YYYY-MM-DD`. `max_results` requests 1 to 20 posts in the answer.
A retrieved sample is not a complete account archive.

The default request allows up to five native search calls.
The bridge omits `max_output_tokens` unless you set `X_SEARCH_MAX_OUTPUT_TOKENS`.
Magpie and the model still apply their own output limits.
Environment settings can change both limits. The server does not retry upstream requests automatically.

The result contains these fields:

| Field | Meaning |
| --- | --- |
| `text` | Grok's summary, with source links |
| `posts` | Structured posts with `url`, `text`, `author`, `created_at`, and `kind` |
| `citations` | X URLs from native citation metadata |
| `search_usage` | Native search, fetched-post, and fetched-user counts |
| `backend_model` | The model that magpie served |
| `retrieved_at` | UTC time of the original retrieval |
| `cache` | Cache status and result age in seconds |

`kind` is `original`, `reply`, `quote`, `repost`, or `unknown`.
Unknown authors or dates are `null`. Profile-only searches can return no posts.

Each structured post must match a post ID in native citation metadata.
The server rejects posts without matching citations. Post fields and the summary
remain model extractions; a matching URL does not verify every statement.

The server returns an MCP tool error when native search execution is unconfirmed.
The server also returns explicit errors for malformed output, truncated output,
connection failures, rate limits, cache failures, and total timeouts.

## Cache and cancellation

The cache uses a local SQLite database. Results expire after 300 seconds by default.
The database retains at most 1000 completed entries. The cache key includes the
query, filters, model, endpoint, output schema, and search limits.

| `cache_mode` | Behavior |
| --- | --- |
| `use` | Return a valid cached result, or join an equivalent search in progress |
| `refresh` | Ignore completed cached results; join an equivalent search in progress or start a search |
| `bypass` | Make an independent request without cache reads, writes, or shared execution |

Cache status is `miss`, `hit`, `coalesced`, or `bypass`.
A cache hit keeps the original `retrieved_at` and `search_usage` values.
Those counts describe the original retrieval; a cache hit makes no upstream request.
Use `refresh` when you need a fresh lookup.

Processes that share a cache path also share in-progress searches. The executor
renews its database lease while the request runs. After a process exits unexpectedly,
its lease expires within 15 seconds. A waiting request can then become the executor.

A failed search supplies the same error to callers that already await that search.
A new explicit invocation can try again. The server does not serve old failures
as normal cached results.

A caller's total timeout includes queue time, cache wait, and network operations.
MCP cancellation stops that caller and suppresses its response. Cancelling a
waiting caller does not stop another caller's search. Cancelling the executor
closes its connection and releases its lease. Remaining callers can start a search.

The server closes active connections when stdin closes or a request stops.
Magpie and xAI determine how quickly their own work stops after disconnection.

## Configuration

Set environment variables on the MCP server process, then restart the MCP client.
The server validates settings before accepting requests.

| Variable | Default | Meaning |
| --- | --- | --- |
| `X_SEARCH_MAGPIE_URL` | `http://127.0.0.1:3425/v1/responses` | Local magpie Responses endpoint |
| `X_SEARCH_MODEL` | `grok-plugin/grok-4.7` | Grok model served by magpie |
| `X_SEARCH_MAX_TOOL_CALLS` | `5` | Native search call limit, 1 to 100 |
| `X_SEARCH_MAX_OUTPUT_TOKENS` | Unset | Optional output token limit, 256 to 32768; omitted from requests by default |
| `X_SEARCH_TIMEOUT_SEC` | `150` | Total request timeout, 1 to 3600 seconds |
| `X_SEARCH_WORKERS` | `4` | Concurrent searches per process, 1 to 32 |
| `X_SEARCH_CACHE_TTL_SEC` | `300` | Cache lifetime; `0` disables caching and shared execution |
| `X_SEARCH_CACHE_PATH` | OS cache directory plus `magpie-x-search/cache.sqlite3` | Shared SQLite database path |

The cache directory uses `XDG_CACHE_HOME`, then `LOCALAPPDATA`, then `~/.cache`.
All terminals that should share searches must use the same database path.
The database must be on a local filesystem.

The bridge connects only to a loopback HTTP endpoint. Magpie supplies upstream
credentials and proxy settings. The repository contains no login credentials.
The bridge requires no magpie middleware.

## Maintenance and validation

The source files are at the repository root:

| File | Responsibility |
| --- | --- |
| `x_search_mcp.py` | MCP lifecycle, errors, concurrent calls, and cancellation notifications |
| `search_backend.py` | Settings, query validation, magpie transport, SSE parsing, and result validation |
| `search_cache.py` | Shared cache, database leases, and duplicate request merging |

Run the offline tests:

```sh
python3 -m unittest discover -v
```

Tests use local fixtures. Tests do not need a Grok account or call an external API.
Tests cover malformed RPC, cancellation, total deadlines, rate limits, structured
sources, cache expiry, failed searches, and duplicate requests across processes.
GitHub Actions runs the tests on Linux with Python 3.10, 3.12, and 3.14,
and on Windows with Python 3.12.

Version 1.0 was tested on 2026-10-06 with magpie 0.1.1082,
Grok OAuth plugin 0.1.8, and Codex 0.160.1. GPT-6.1 Sol was the calling model.
Version 1.1 was tested against the same magpie and OAuth plugin on 2026-10-06.
Grok returned two structured posts with native citations. GPT-6.1 Sol then
read both posts through MCP and confirmed two cache hits with the same retrieval time.

Version 1.1 adds structured output, shared cache, concurrent calls, and cancellation.

References:

- [xAI native X search](https://docs.x.ai/developers/tools/x-search)
- [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)
- [magpie plugin interface](https://usemagpie.ai/docs/plugins)
- [MCP cancellation](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)
