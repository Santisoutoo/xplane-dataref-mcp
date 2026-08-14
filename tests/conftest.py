"""Fixtures: a canned X-Plane Web API, and an MCP client.

X-Plane is faked at the HTTP boundary — an ``httpx.MockTransport`` serving
canned JSON — because that boundary is exactly what this repo owns. What a
dataref *means* belongs to Laminar's documentation; what belongs here is
that a catalogue is downloaded and searched correctly, that a value crosses
back intact through MCP serialization, and that an error arrives as a
sentence.

The canned catalogue is five datarefs and two commands with real X-Plane
names, chosen to exercise every value type the tools treat specially: a
plain float, a float array (for ``index``), and a ``data`` byte array that
decodes to a tail number.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp import Client

from xplane_dataref_mcp.client import XPlaneClient, reset_client, set_client
from xplane_dataref_mcp.server import build_server

#: What the ``mcp_client`` fixture hands back: call it, then ``async with`` it.
ClientFactory = Callable[[], AbstractAsyncContextManager[Client]]

#: Never resolved — every request dies in the MockTransport.
XPLANE_URL = "http://xplane.test"


# ---------------------------------------------------------------------------
# Canned payloads (the wire truth these tests assert against)
# ---------------------------------------------------------------------------

CAPABILITIES = {
    "api": {"versions": ["v1", "v2"]},
    "x-plane": {"version": "12.1.4-r3"},
}

DATAREFS = [
    {
        "id": 1,
        "name": "sim/cockpit2/gauges/indicators/airspeed_kts_pilot",
        "value_type": "float",
        "is_writable": False,
    },
    {
        "id": 2,
        "name": "sim/cockpit2/gauges/indicators/airspeed_kts_copilot",
        "value_type": "float",
        "is_writable": False,
    },
    {
        "id": 3,
        "name": "sim/flightmodel/position/latitude",
        "value_type": "double",
        "is_writable": False,
    },
    {
        "id": 4,
        "name": "sim/aircraft/view/acf_tailnum",
        "value_type": "data",
        "is_writable": True,
    },
    {
        "id": 5,
        "name": "sim/cockpit2/engine/indicators/N1_percent",
        "value_type": "float_array",
        "is_writable": False,
    },
]

COMMANDS = [
    {
        "id": 10,
        "name": "sim/flight_controls/landing_gear_toggle",
        "description": "Toggle the landing gear",
    },
    {
        "id": 11,
        "name": "sim/autopilot/heading",
        "description": "Autopilot heading select or hold",
    },
]

#: ``acf_tailnum`` on the wire: a NUL-padded byte array, base64-encoded.
TAILNUM_B64 = base64.b64encode(b"EC-ABC\x00\x00\x00").decode("ascii")

VALUES: dict[int, Any] = {
    1: 123.5,
    3: 40.472,
    4: TAILNUM_B64,
    5: [95.2, 94.8],
}


# ---------------------------------------------------------------------------
# The fake simulator
# ---------------------------------------------------------------------------


class FakeXPlane:
    """X-Plane's Web API, reduced to canned HTTP responses.

    Every request the client makes is recorded in :attr:`requests`, so a test
    can assert what actually crossed the wire. The catalogue endpoints honour
    ``start`` / ``limit`` by slicing the canned lists, which is what lets the
    pagination loop be tested with a shrunken page size.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.datarefs = [dict(row) for row in DATAREFS]
        self.commands = [dict(row) for row in COMMANDS]
        self.values = dict(VALUES)
        self._overrides: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def respond(self, method: str, path: str, *, status: int = 200, json: Any = None) -> None:
        """Serve ``json`` with ``status`` for every ``method path`` request."""
        self._overrides[(method, path)] = lambda _request: httpx.Response(status, json=json)

    def refuse(self, method: str, path: str) -> None:
        """Make ``method path`` fail as if X-Plane were not running."""

        def _raise(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("All connection attempts failed")

        self._overrides[(method, path)] = _raise

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        override = self._overrides.get((request.method, path))
        if override is not None:
            return override(request)
        if request.method == "GET" and path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if request.method == "GET" and path == "/api/v2/datarefs":
            return self._page(request, self.datarefs)
        if request.method == "GET" and path == "/api/v2/commands":
            return self._page(request, self.commands)
        if request.method == "GET" and path.startswith("/api/v2/datarefs/"):
            return self._value(request)
        if request.method == "POST" and path.startswith("/api/v2/command/"):
            ident = int(path.removeprefix("/api/v2/command/").removesuffix("/activate"))
            if any(row["id"] == ident for row in self.commands):
                return httpx.Response(200, json={})
            return httpx.Response(404, json={"error_message": "unknown command id"})
        # a canned suite must never 404 by accident
        raise AssertionError(f"unexpected request: {request.method} {path}")

    def _page(self, request: httpx.Request, rows: list[dict[str, Any]]) -> httpx.Response:
        params = request.url.params
        start = int(params.get("start", 0))
        limit = int(params.get("limit", len(rows)))
        return httpx.Response(200, json={"data": rows[start : start + limit]})

    def _value(self, request: httpx.Request) -> httpx.Response:
        ident = int(request.url.path.removeprefix("/api/v2/datarefs/").removesuffix("/value"))
        if ident not in self.values:
            return httpx.Response(404, json={"error_message": "unknown dataref id"})
        value = self.values[ident]
        index = request.url.params.get("index")
        if index is not None and isinstance(value, list):
            value = value[int(index)]
        return httpx.Response(200, json={"data": value})

    @property
    def last_request(self) -> httpx.Request:
        assert self.requests, "no request reached the fake simulator"
        return self.requests[-1]


@pytest.fixture(autouse=True)
async def sim() -> AsyncIterator[FakeXPlane]:
    """Wire the shared client to a fresh fake X-Plane for every test.

    Injected via ``set_client``, which also marks the client as test-owned so
    the server lifespan's teardown leaves it alone; ``reset_client`` here is
    what actually closes it.
    """
    fake = FakeXPlane()
    set_client(XPlaneClient(XPLANE_URL, transport=httpx.MockTransport(fake.handler)))
    yield fake
    await reset_client()


@asynccontextmanager
async def _connected_client() -> AsyncIterator[Client]:
    """An MCP client talking to an in-process server over memory streams."""
    async with Client(build_server()) as connected:
        yield connected


@pytest.fixture
def mcp_client() -> ClientFactory:
    """Open a client inside the test: ``async with mcp_client() as client:``.

    Going through a real client exercises what a model actually sees — schema
    generation, serialization, and the error mapping — rather than just calling
    the Python functions underneath.

    **The fixture hands back a factory rather than an open client, and that is
    not a style choice.** The client holds an anyio task group open across its
    ``with`` body, and pytest-asyncio finalises an async fixture in a *different*
    task from the one that set it up — which trips anyio's "attempted to exit
    cancel scope in a different task" guard on every single teardown. Opening it
    inside the test body keeps entry and exit in one task.
    """
    return _connected_client
