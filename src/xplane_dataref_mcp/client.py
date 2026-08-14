"""The HTTP client this whole server is built on.

One class, one wrapped request method, two exception types. Every tool call
becomes HTTP requests to X-Plane's own Web API (12.1.4+, ``/api/v2``) — no
plugin, no UDP, no third process. Every way a request can fail is translated
here into an exception whose ``str()`` is a sentence an operator (or a
model) can act on:

* :class:`XPlaneUnreachable` — the simulator is not answering. Names the URL
  and says how to enable the Web API. "Not running yet" is a routine state,
  not a bug.
* :class:`XPlaneResponseError` — X-Plane answered with an HTTP error; its
  body is carried through.

Both derive from ``RuntimeError`` so :mod:`xplane_dataref_mcp.tools` keeps a
single backstop.

The catalogue
-------------

X-Plane publishes ~10,000 datarefs and ~3,000 commands, addressed on the
wire by numeric ids that are only stable within one simulator run. The
client downloads each catalogue once, caches it as a name→entry map, and
resolves names to ids locally — which is also what makes substring search
possible at all, since the API itself only filters by exact name. A cache
grown stale (X-Plane restarted, ids reshuffled) surfaces as a 404 on a read;
the caller refreshes and retries once.
"""

from __future__ import annotations

import base64
import binascii
import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "DEFAULT_URL",
    "CatalogueEntry",
    "XPlaneClient",
    "XPlaneResponseError",
    "XPlaneUnreachable",
    "decode_value",
    "get_client",
    "release_lazy_client",
    "reset_client",
    "set_client",
]

DEFAULT_URL = "http://127.0.0.1:8086"

#: Connect is kept short so "X-Plane is not running" fails fast into a clear
#: sentence; read allows for the catalogue download, which is a few MB of
#: JSON on a machine that is also rendering a flight simulator.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

#: Page size for catalogue downloads. The API paginates with ``start`` /
#: ``limit``; one page of 5000 keeps the request count at three or four.
_PAGE = 5000

#: Hard ceiling on catalogue pages — a backstop against a server that
#: ignores pagination and returns the same page forever.
_MAX_PAGES = 40


class XPlaneUnreachable(RuntimeError):
    """X-Plane did not answer at all (refused, timed out, DNS…)."""

    def __init__(self, base_url: str, cause: Exception) -> None:
        super().__init__(
            f"X-Plane is not reachable at {base_url} — is it running with the Web API "
            f"enabled? Needs X-Plane 12.1.4+ with Settings → Network → 'Accept incoming "
            f"connections' on, or point XPLANE_URL at it. ({cause})"
        )
        self.base_url = base_url


class XPlaneResponseError(RuntimeError):
    """X-Plane answered with an HTTP error; ``detail`` is its body."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class CatalogueEntry(BaseModel):
    """One dataref or command as the catalogue endpoints list it.

    ``extra="ignore"`` on purpose: the Web API grows fields across X-Plane
    releases, and a catalogue download must not start failing because 12.x
    added one.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    value_type: str | None = None  # datarefs only
    is_writable: bool | None = None  # datarefs only, newer X-Plane versions
    description: str | None = None  # commands only


class XPlaneClient:
    """A thin wrapper over X-Plane's Web API, plus the catalogue caches.

    ``transport`` exists for the tests: an ``httpx.MockTransport`` makes the
    whole client run against canned responses without a socket.
    """

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=_TIMEOUT,
            transport=transport,
        )
        self._datarefs: dict[str, CatalogueEntry] | None = None
        self._commands: dict[str, CatalogueEntry] | None = None

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._http.aclose()

    # -- the one place every request goes through ---------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._http.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            raise XPlaneUnreachable(self.base_url, exc) from exc
        if response.is_error:
            raise XPlaneResponseError(response.status_code, _detail_of(response))
        if not response.content:
            return None
        return response.json()

    # -- capabilities --------------------------------------------------------

    async def capabilities(self) -> dict[str, Any]:
        """``GET /api/capabilities`` — unversioned, shape left to X-Plane.

        Returned as a plain dict: this endpoint exists to be shown to a
        human ("which API versions, which X-Plane"), not to be depended on
        field by field.
        """
        payload = await self._request("GET", "/api/capabilities")
        return payload if isinstance(payload, dict) else {"raw": payload}

    # -- catalogues ----------------------------------------------------------

    async def datarefs(self, *, refresh: bool = False) -> dict[str, CatalogueEntry]:
        """The dataref catalogue, downloaded once and cached by name."""
        if self._datarefs is None or refresh:
            self._datarefs = await self._catalogue("/api/v2/datarefs")
        return self._datarefs

    async def commands(self, *, refresh: bool = False) -> dict[str, CatalogueEntry]:
        """The command catalogue, downloaded once and cached by name."""
        if self._commands is None or refresh:
            self._commands = await self._catalogue("/api/v2/commands")
        return self._commands

    async def _catalogue(self, path: str) -> dict[str, CatalogueEntry]:
        entries: dict[str, CatalogueEntry] = {}
        start = 0
        for _ in range(_MAX_PAGES):
            payload = await self._request("GET", path, params={"start": start, "limit": _PAGE})
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            page = [_entry_of(row) for row in rows]
            before = len(entries)
            entries.update((entry.name, entry) for entry in page if entry is not None)
            # Both exits matter: a short page is the normal end, and a page
            # that added nothing new means the server ignored `start`.
            if len(page) < _PAGE or len(entries) == before:
                break
            start += len(page)
        return entries

    # -- values and commands -------------------------------------------------

    async def dataref_value(self, entry: CatalogueEntry, index: int | None = None) -> Any:
        """``GET /api/v2/datarefs/{id}/value`` — raw, undecoded."""
        params = {} if index is None else {"index": index}
        payload = await self._request("GET", f"/api/v2/datarefs/{entry.id}/value", params=params)
        return payload.get("data") if isinstance(payload, dict) else payload

    async def activate_command(self, entry: CatalogueEntry, duration: float) -> None:
        """``POST /api/v2/command/{id}/activate``."""
        await self._request(
            "POST", f"/api/v2/command/{entry.id}/activate", json={"duration": duration}
        )


def _entry_of(row: Any) -> CatalogueEntry | None:
    """One catalogue row, or ``None`` for a malformed one.

    A single odd row (SDK plugins can publish surprising names) must not
    poison a 10,000-entry download.
    """
    try:
        return CatalogueEntry.model_validate(row)
    except ValidationError:
        return None


def decode_value(value: Any, value_type: str | None) -> Any:
    """A dataref value as a model should see it.

    Numeric types pass through. ``data`` datarefs arrive base64-encoded —
    they are byte arrays, in practice almost always NUL-padded text (tail
    numbers, livery paths) — so they are decoded to a string when they are
    one, and left as the base64 X-Plane sent otherwise.
    """
    if value_type != "data" or not isinstance(value, str):
        return value
    try:
        raw = base64.b64decode(value, validate=True)
        return raw.rstrip(b"\x00").decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return value


def _detail_of(response: httpx.Response) -> str:
    """The error sentence of an X-Plane response, however it was packaged."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("error_message", "error", "detail", "message"):
            detail = body.get(key)
            if isinstance(detail, str) and detail:
                return detail
    return response.text or f"HTTP {response.status_code} with an empty body"


# ---------------------------------------------------------------------------
# The singleton the tools share
# ---------------------------------------------------------------------------

_client: XPlaneClient | None = None
_client_was_injected = False


def get_client() -> XPlaneClient:
    """The shared client, built lazily from ``XPLANE_URL`` on first use."""
    global _client, _client_was_injected
    if _client is None:
        _client = XPlaneClient(os.environ.get("XPLANE_URL", DEFAULT_URL))
        _client_was_injected = False
    return _client


def set_client(client: XPlaneClient) -> None:
    """Inject a client (tests). An injected client is never closed or dropped
    by the server's lifespan — whoever injected it owns its lifetime."""
    global _client, _client_was_injected
    _client = client
    _client_was_injected = True


async def release_lazy_client() -> None:
    """Close and drop the singleton only if :func:`get_client` built it."""
    global _client
    if _client is not None and not _client_was_injected:
        client = _client
        _client = None
        await client.aclose()


async def reset_client() -> None:
    """Close and drop the singleton unconditionally (test teardown)."""
    global _client, _client_was_injected
    client = _client
    _client, _client_was_injected = None, False
    if client is not None:
        await client.aclose()
