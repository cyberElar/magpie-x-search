# magpie-x-search

An MCP `x_search` tool that lets any model search public X/Twitter content
through Grok's native xAI search and an existing local magpie OAuth login.
The calling model can be GPT, Claude, DeepSeek, or any other MCP client.
Grok runs only the requested retrieval; the calling model receives the sourced
result and performs the analysis.

## Setup

Requires Python 3.10+, a running [magpie](https://usemagpie.ai), and a signed-in
Grok provider exposing `grok-plugin/grok-4.7`. No Python packages are needed.
Set up the Grok subscription in magpie using
`@magpie-community/opencode-grok-auth`. The existing plugin handles OAuth,
refreshes and upstream proxy settings. This repository does not store tokens.

Clone this repository into a single project folder and enter it. Register it
with Codex from the repository root:

```sh
codex mcp add x-search -- python3 "$PWD/x_search_mcp.py"
```

Start a new Codex session so it discovers the server. Ask any model to use
`x_search` to research a public X account or topic.

For longer investigations, set `tool_timeout_sec = 180` in the
`[mcp_servers.x-search]` section of Codex's `config.toml`.

Other MCP clients can use this configuration, replacing the path with their
own clone location:

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

Each machine uses its own magpie login. Multiple terminals on the same
machine share that machine's magpie gateway.

## Tool

```json
{
  "query": "from:example Find three recent public original posts; include dates and links.",
  "allowed_x_handles": ["example"],
  "from_date": "2026-10-01",
  "to_date": "2026-10-06",
  "max_results": 3
}
```

Only `query` is required. Handle filters accept up to 20 handles. Dates use
`YYYY-MM-DD`. `max_results` requests 1–20 results in the summary; upstream
search can fetch more. This is a retrieved sample, not a complete timeline.

The result includes:

- `text`: Grok's summary of the retrieved content.
- `citations`: original X URLs from native citation metadata.
- `search_usage`: actual X search, fetched-post and fetched-user counts.
- `backend_model` and `retrieved_at`: the served model and UTC retrieval time.

The server returns an explicit MCP error when native X search did not execute.
It does not treat an unsourced model answer as a successful search. A nonzero
search count confirms execution, but does not guarantee every summary claim;
use the citations to verify important findings.

Optional environment settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `X_SEARCH_MAGPIE_URL` | `http://127.0.0.1:3425/v1/responses` | Local magpie Responses endpoint |
| `X_SEARCH_MODEL` | `grok-plugin/grok-4.7` | Grok model served by magpie |

The bridge only connects to a loopback HTTP endpoint. It has no API key,
filesystem tool or command runner. Search uses the Grok account's normal
allowance and is limited to content accessible through native X search.
Each explicit tool invocation makes one upstream request, with
`max_tool_calls: 1` and a 1600-token output cap. There are no automatic retries.
Keep follow-up analysis in the calling model to conserve Grok allowance.

The MCP bridge aggregates Grok's final response and returns a normal MCP
result. Native search events are handled inside the bridge rather than
exposed as client tool calls. It needs no magpie middleware and does not
change other model requests or the installed OAuth plugin.

Remove Codex's MCP registration with `codex mcp remove x-search`.

## Validation

```sh
python3 test_x_search.py
```

Tested with magpie 0.1.1082, Grok OAuth plugin 0.1.8 and Codex 0.160.1.
Tests cover MCP initialization, argument validation, missing-search errors,
SSE completion, source metadata and the per-call search budget.

An end-to-end test on 2026-10-06 used **GPT-6.1 Sol**, rather than Grok,
as the calling model inside Codex. Its single MCP invocation succeeded;
the native backend reported `x_search_calls: 1`, `x_posts_fetched: 1`,
and returned an original X URL in citation metadata.

References: [xAI native X search](https://docs.x.ai/developers/tools/x-search),
[magpie plugin and middleware interface](https://usemagpie.ai/docs/plugins),
[MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
