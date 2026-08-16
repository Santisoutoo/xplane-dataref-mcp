# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-08-16

First release.

### Added

- Six MCP tools over X-Plane 12's Web API (12.1.4+): `get_connection_status`,
  `search_datarefs`, `read_dataref`, `read_datarefs`, `search_commands`,
  `execute_command`.
- Local substring search (AND of terms, case-insensitive) over the cached
  dataref and command catalogues — the Web API itself only filters by exact
  name.
- `stdio` (default) and `streamable-http` transports; `--host`/`--port` flags
  for the latter.
- Transparent stale-id recovery: a 404 on a known id refreshes the catalogue
  and retries once, so an X-Plane restart mid-session is invisible.
- Base64 `data` datarefs decoded to text when they are text.
- One configuration knob: `XPLANE_URL` (default `http://127.0.0.1:8086`).

[0.1.0]: https://github.com/Santisoutoo/xplane-dataref-mcp/releases/tag/v0.1.0
