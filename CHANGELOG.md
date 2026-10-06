# Changelog

## 1.2.0 - 2026-10-07

- Add the `magpie-x-search` npm command and `npx` support.
- Detect Python 3.10+ on Linux, macOS, and Windows.
- Add `X_SEARCH_PYTHON`, `--help`, and `--version`.
- Forward standard input, output, errors, and shutdown signals to the Python server.
- Restrict npm package contents to runtime files, README, metadata, and the MIT license.
- Test packaged installation, MCP communication, UTF-8, and paths with spaces.
- Add a manual GitHub Actions workflow for npm OIDC publishing with provenance.

## 1.1.0 - 2026-10-06

- Add structured posts checked against native citation metadata.
- Add shared SQLite caching and duplicate request merging across processes.
- Add concurrent MCP calls, cancellation, and total request deadlines.
- Make the output token limit optional; requests omit the limit by default.
- Add offline transport, protocol, and cache tests on Linux and Windows.

## 1.0.0 - 2026-10-06

- Add native Grok X search through the local magpie Responses endpoint.
- Expose search results to any MCP calling model.
