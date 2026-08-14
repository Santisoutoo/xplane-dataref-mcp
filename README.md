# xplane-dataref-mcp

An [MCP](https://modelcontextprotocol.io) server that gives an LLM client raw access to a running
X-Plane 12: search its ~10,000 datarefs and ~3,000 commands by substring, read any dataref's
current value, and fire commands — over X-Plane's own Web API. No plugin, no UDP, no third
process.

```
you ──▶ Claude ──▶ xplane-dataref-mcp ──HTTP──▶ X-Plane 12 Web API
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
| `read_datarefs` | Several at once — one snapshot of related state. |
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

```bash
uv tool install --from git+https://github.com/Santisoutoo/xplane-dataref-mcp xplane-dataref-mcp
claude mcp add xplane-datarefs -- xplane-dataref-mcp
```

Or, without installing anything, as a project-scoped `.mcp.json`:

```json
{
  "mcpServers": {
    "xplane-datarefs": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Santisoutoo/xplane-dataref-mcp", "xplane-dataref-mcp"]
    }
  }
}
```

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
download, the search, the base64 decode and the stale-id retry are all exercised without a
simulator. The `sim` marker is reserved for tests that need the real thing.

## Licence

Not chosen yet. Until then, all rights reserved.
