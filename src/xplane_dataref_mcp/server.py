"""The MCP server instance and its lifespan.

This process is a pure HTTP client of X-Plane's own Web API. The one knob
it has is ``XPLANE_URL`` — where the simulator is serving (default
``http://127.0.0.1:8086``). Everything else — which aircraft, which
plugins, and therefore which datarefs exist — is X-Plane's business.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from xplane_dataref_mcp.client import get_client, release_lazy_client
from xplane_dataref_mcp.tools import register_all

__all__ = ["build_server", "mcp"]

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Raw access to a running X-Plane 12: search its ~10,000 datarefs (simulator \
state) and ~3,000 commands (momentary actions) by substring, read dataref \
values, and fire commands, over X-Plane's own Web API.

X-Plane is a separate process reached over HTTP at XPLANE_URL. If tools fail \
saying it is not reachable, it needs to be running with the Web API enabled \
(12.1.4+, Settings → Network → 'Accept incoming connections') — \
get_connection_status is how you ask without failing.

The workflow is search first, then act: dataref and command names are exact, \
undiscoverable strings, so find them with search_datarefs / search_commands \
before read_dataref / execute_command. When building a picture of related \
state (an autopilot, an engine), read_datarefs takes the whole list in one \
call. execute_command acts on a live simulator someone may be flying — fire \
only what the user asked for.
"""


@asynccontextmanager
async def _lifespan(_server: MCPServer[None]) -> AsyncIterator[None]:
    """Ping X-Plane at startup, but never refuse to start.

    The simulator is routinely down when the client launches this server —
    an MCP client starts its servers immediately. A server that exited here
    would be unavailable for the rest of the session, so startup logs the
    situation and carries on; every tool call is its own HTTP request, and
    ``get_connection_status`` is how a caller asks what is reachable
    without failing.
    """
    client = get_client()
    try:
        await client.capabilities()
    except Exception as exc:  # startup must survive an absent simulator
        logger.warning("Starting without a reachable X-Plane at %s: %s", client.base_url, exc)
    try:
        yield
    finally:
        # Only a client this process lazily built is closed here: a
        # test-injected client belongs to whoever injected it.
        await release_lazy_client()


def build_server() -> MCPServer[None]:
    """Construct the server with every tool registered."""
    server: MCPServer[None] = MCPServer(
        name="xplane-dataref-mcp",
        title="X-Plane datarefs and commands",
        instructions=INSTRUCTIONS,
        version="0.1.0",
        lifespan=_lifespan,
    )
    register_all(server)
    return server


mcp = build_server()
