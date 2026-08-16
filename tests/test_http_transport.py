"""The streamable-http transport, exercised over real sockets.

The in-memory suite in ``test_tools.py`` proves the tools; what it cannot
prove is that the HTTP transport a network client actually connects to
serves them — uvicorn, the Starlette app, the session manager's lifespan,
and the wire serialization in between. So these tests boot the real thing
on an ephemeral port and connect to it by URL, exactly as a programmatic
agent would.

The autouse ``sim`` fixture has already injected the canned X-Plane, so
the served instance answers with known data and no simulator.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from mcp import Client

from tests.test_tools import EXPECTED_TOOLS
from xplane_dataref_mcp.__main__ import _build_parser
from xplane_dataref_mcp.server import build_server


@asynccontextmanager
async def _served_url() -> AsyncIterator[str]:
    """The server under uvicorn on an ephemeral port; yields the MCP URL.

    ``port=0`` lets the OS pick a free port, which is what keeps this suite
    parallel-safe and CI-safe; the real port is read back off the bound
    socket. Shutdown is uvicorn's own (``should_exit``), so the lifespan —
    and with it the session manager — winds down cleanly.
    """
    app = build_server().streamable_http_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if task.done():
                task.result()  # surfaces the startup failure instead of hanging
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


async def test_every_tool_is_served_over_http() -> None:
    async with _served_url() as url, Client(url) as client:
        listing = await client.list_tools()
    assert {tool.name for tool in listing.tools} == EXPECTED_TOOLS


async def test_a_value_crosses_the_http_wire() -> None:
    """One full round trip: URL → uvicorn → tool → X-Plane fake → back."""
    async with _served_url() as url, Client(url) as client:
        result = await client.call_tool(
            "read_dataref",
            {"name": "sim/cockpit2/gauges/indicators/airspeed_kts_pilot"},
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["value"] == 123.5


def test_parser_defaults_match_the_docs() -> None:
    args = _build_parser().parse_args([])
    assert (args.transport, args.host, args.port) == ("stdio", "127.0.0.1", 8000)


def test_parser_accepts_http_flags() -> None:
    args = _build_parser().parse_args(
        ["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"]
    )
    assert (args.transport, args.host, args.port) == ("streamable-http", "0.0.0.0", 9000)
