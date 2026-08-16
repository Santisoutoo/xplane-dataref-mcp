# xplane-dataref-mcp

[![CI](https://github.com/Santisoutoo/xplane-dataref-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Santisoutoo/xplane-dataref-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/xplane-dataref-mcp)](https://pypi.org/project/xplane-dataref-mcp/)

An [MCP](https://modelcontextprotocol.io) server that gives an LLM client raw access to a running
X-Plane 12: search its ~10,000 datarefs and ~3,000 commands by substring, read any dataref's
current value, and fire commands — over X-Plane's own Web API. No plugin, no UDP, no third
process.

```
you ──▶ any MCP client ──▶ xplane-dataref-mcp ──HTTP──▶ X-Plane 12 Web API
```

This is the low-level sibling of [xplane-mcp](https://github.com/Santisoutoo/xplane-mcp), which
speaks instructor-level operations ("place me on a 10 NM final") through the Open Instructor
Station. This server speaks X-Plane's own vocabulary instead, and its reason to exist is mostly
the lookup problem: nobody knows the string
`sim/cockpit2/gauges/indicators/airspeed_kts_pilot` before finding it, and the Web API itself
only filters by exact name. The server downloads the catalogue once and searches it locally, so
"what's my airspeed?" becomes a search, a read, and an answer.

## What it can do

| Tool | |
|---|---|
| `get_connection_status` | Is X-Plane reachable, and what does its API offer? Reported as data, not as an error. |
| `search_datarefs` | Substring search (AND of terms, case-insensitive) over every dataref name. |
| `read_dataref` | One dataref's current value, by exact name; `index` picks an array element. |
| `read_datarefs` | Several at once, read concurrently — one snapshot of related state. |
| `search_commands` | The same search over command names and descriptions. |
| `execute_command` | Press a command, or hold it for up to 10 s. |

Byte-array (`data`) datarefs are decoded from base64 to text when they are text — a tail number
reads as `EC-ABC`, not `RUMtQUJD…`. Dataref ids go stale when X-Plane restarts mid-session;
the server notices the 404, refreshes its catalogue and retries, invisibly.

Deliberately absent: writing datarefs. Commands cover the "act on the simulator" cases with
X-Plane's own semantics and bounds; raw dataref writes are a different risk class, and
instructor-style state changes already have a home in
[xplane-mcp](https://github.com/Santisoutoo/xplane-mcp).

## X-Plane setup

Needs X-Plane **12.1.4 or newer** (the `/api/v2` API — commands arrived there). Enable the Web
API in Settings → Network → *Accept incoming connections*. X-Plane then serves on port 8086
(`--web_server_port=NNNN` to change it).

The server itself starts fine with the simulator down: every tool call is its own HTTP request,
so the moment X-Plane comes up the tools work, with no restart. While it is down, tools fail
with a sentence saying where they looked and how to fix it, and `get_connection_status` is how
a caller asks without failing.

## Install

With [uv](https://docs.astral.sh/uv/) there is nothing to install ahead of time — every client
config below just runs `uvx xplane-dataref-mcp`, which fetches it from PyPI on first use. For a
persistent install:

```bash
uv tool install xplane-dataref-mcp    # or: pip install xplane-dataref-mcp
```

From source: `uvx --from git+https://github.com/Santisoutoo/xplane-dataref-mcp xplane-dataref-mcp`.

## Use with your client

Every stdio config is the same idea — `command: uvx`, `args: ["xplane-dataref-mcp"]` — dressed
in each client's file format. If X-Plane is not at the default `http://127.0.0.1:8086`, add
`XPLANE_URL` via the config's `env` field (shown once, in the Claude Code example).

### Claude Code

```bash
claude mcp add xplane-datarefs -- uvx xplane-dataref-mcp
```

Or as a project-scoped `.mcp.json`:

```json
{
  "mcpServers": {
    "xplane-datarefs": {
      "command": "uvx",
      "args": ["xplane-dataref-mcp"],
      "env": { "XPLANE_URL": "http://127.0.0.1:8086" }
    }
  }
}
```

### Claude Desktop

Settings → Developer → Edit Config, then in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "xplane-datarefs": { "command": "uvx", "args": ["xplane-dataref-mcp"] }
  }
}
```

### Cursor

`~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per project):

```json
{
  "mcpServers": {
    "xplane-datarefs": { "command": "uvx", "args": ["xplane-dataref-mcp"] }
  }
}
```

### VS Code (GitHub Copilot)

`.vscode/mcp.json` — note the different shape (`servers`, and a `type`):

```json
{
  "servers": {
    "xplane-datarefs": { "type": "stdio", "command": "uvx", "args": ["xplane-dataref-mcp"] }
  }
}
```

### Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.xplane-datarefs]
command = "uvx"
args = ["xplane-dataref-mcp"]
```

### Gemini CLI

`~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "xplane-datarefs": { "command": "uvx", "args": ["xplane-dataref-mcp"] }
  }
}
```

### LM Studio

Program → Install → Edit `mcp.json`:

```json
{
  "mcpServers": {
    "xplane-datarefs": { "command": "uvx", "args": ["xplane-dataref-mcp"] }
  }
}
```

## HTTP for programmatic agents

Agents built in code — rather than launched by a desktop client — connect over streamable HTTP
instead of stdio:

```bash
xplane-dataref-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. With the MCP Python SDK:

```python
from mcp import Client

async with Client("http://127.0.0.1:8000/mcp") as client:
    tools = await client.list_tools()
```

With the OpenAI Agents SDK:

```python
from agents.mcp import MCPServerStreamableHttp

xplane = MCPServerStreamableHttp(params={"url": "http://127.0.0.1:8000/mcp"})
```

**Security**: the server has no authentication. On `127.0.0.1` the SDK's Host/Origin validation
(DNS-rebinding protection) is enabled automatically; binding `0.0.0.0` disables it and hands
control of your simulator to anyone who can reach the port. Trusted networks only.

## Configuration

One variable.

| Variable | Default | |
|---|---|---|
| `XPLANE_URL` | `http://127.0.0.1:8086` | Where X-Plane's Web API is serving. |

X-Plane's Web API is unauthenticated, so point `XPLANE_URL` only at machines on networks you
trust.

## Development

```bash
git clone https://github.com/Santisoutoo/xplane-dataref-mcp && cd xplane-dataref-mcp
uv venv
uv pip install -e ".[dev]"
```

```bash
pytest                       # offline: every test runs against a canned Web API
pytest -m sim                # against a real X-Plane at XPLANE_URL
ruff check . && ruff format --check . && mypy .
```

The default suite fakes X-Plane at the HTTP boundary with canned responses — the catalogue
download, the search, the base64 decode, the stale-id retry, and the streamable-http transport
itself are all exercised without a simulator. The `sim` marker is reserved for tests that need
the real thing.

## Licence

[MIT](LICENSE).
