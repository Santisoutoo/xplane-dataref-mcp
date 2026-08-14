"""The tools: search the catalogues, read datarefs, fire commands.

Search happens locally over the cached catalogues because the Web API only
filters by *exact* name — and nobody who needs `sim/cockpit2/gauges/
indicators/airspeed_kts_pilot` knows that string before finding it. That
lookup problem is most of why this server exists.

Every tool body runs inside :func:`_tool_errors`, which maps the client's
exceptions onto :class:`ToolError` so a model is handed a sentence, never a
traceback. The backstop is ``RuntimeError``, not a list of classes, so an
error type added tomorrow is still caught.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from xplane_dataref_mcp.client import (
    CatalogueEntry,
    XPlaneResponseError,
    decode_value,
    get_client,
)

__all__ = ["register_all"]

logger = logging.getLogger(__name__)

#: Search results are capped so a one-letter query cannot flood the model's
#: context with a whole catalogue.
_MAX_RESULTS = 200


@contextmanager
def _tool_errors() -> Iterator[None]:
    """Map the client's exceptions onto :class:`ToolError`."""
    try:
        yield
    except ToolError:
        raise
    except RuntimeError as exc:
        logger.debug("Tool failed", exc_info=exc)
        raise ToolError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Result models — what the LLM actually sees
# ---------------------------------------------------------------------------


class ConnectionStatus(BaseModel):
    """Whether X-Plane is answering — reported as data, not as a failure.

    This is the one tool that is allowed to fail and still return: "X-Plane
    is not running" is the answer to the question, not an error in asking
    it. Every other tool raises instead, because for them an unreachable
    simulator means the work did not happen.
    """

    reachable: bool = Field(description="Whether X-Plane answered at url.")
    url: str = Field(description="Where this server looked for X-Plane (XPLANE_URL).")
    capabilities: dict[str, Any] | None = Field(
        default=None,
        description="X-Plane's own /api/capabilities body: API versions, simulator version.",
    )
    detail: str | None = Field(
        default=None,
        description="Why X-Plane could not be reached, when it could not.",
    )


class DatarefMatch(BaseModel):
    """One dataref, as the search tool reports it."""

    name: str
    value_type: str | None = Field(
        default=None,
        description="float, double, int, int_array, float_array, or data (a byte array).",
    )
    is_writable: bool | None = None


class DatarefSearchResult(BaseModel):
    matches: list[DatarefMatch]
    total_matches: int = Field(
        description="How many datarefs matched in all; matches is truncated to the limit."
    )


class DatarefValue(BaseModel):
    name: str
    value: Any = Field(
        description=(
            "The current value. Arrays arrive whole unless an index was given; "
            "'data' datarefs are decoded from base64 to text when they are text."
        )
    )
    value_type: str | None = None


class CommandMatch(BaseModel):
    """One command, as the search tool reports it."""

    name: str
    description: str | None = None


class CommandSearchResult(BaseModel):
    matches: list[CommandMatch]
    total_matches: int = Field(
        description="How many commands matched in all; matches is truncated to the limit."
    )


class CommandResult(BaseModel):
    name: str
    activated: bool
    duration: float


# ---------------------------------------------------------------------------
# Shared lookup logic
# ---------------------------------------------------------------------------


def _search(
    catalogue: dict[str, CatalogueEntry], query: str, limit: int
) -> tuple[list[CatalogueEntry], int]:
    """Case-insensitive AND-of-terms substring search, most-specific first.

    Every whitespace-separated term must appear in the name (or, for
    commands, the description). Shorter names sort first because the generic
    dataref a caller usually wants (`.../airspeed_kts_pilot`) is shorter
    than its copilot/failure/override cousins.
    """
    terms = [term.lower() for term in query.split() if term]
    if not terms:
        return [], 0
    matched = [
        entry
        for entry in catalogue.values()
        if all(
            term in entry.name.lower() or term in (entry.description or "").lower()
            for term in terms
        )
    ]
    matched.sort(key=lambda entry: (len(entry.name), entry.name))
    return matched[:limit], len(matched)


async def _resolve_dataref(name: str) -> CatalogueEntry:
    """A dataref by exact name, refreshing the catalogue once for a miss.

    The refresh covers datarefs published after the catalogue was cached —
    an aircraft or plugin loaded mid-session brings its own.
    """
    client = get_client()
    entry = (await client.datarefs()).get(name)
    if entry is None:
        entry = (await client.datarefs(refresh=True)).get(name)
    if entry is None:
        raise ToolError(
            f"No dataref is named '{name}' (exact match, case-sensitive). "
            f"Use search_datarefs to find the right name."
        )
    return entry


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all(server: MCPServer[None]) -> None:
    """Register every tool."""

    @server.tool()
    async def get_connection_status() -> ConnectionStatus:
        """Report whether X-Plane is reachable, and what its Web API offers.

        Never fails: an absent simulator comes back as ``reachable: false``
        with the reason and the fix.
        """
        client = get_client()
        try:
            capabilities = await client.capabilities()
        except Exception as exc:  # the failure IS this tool's answer
            return ConnectionStatus(reachable=False, url=client.base_url, detail=str(exc))
        return ConnectionStatus(reachable=True, url=client.base_url, capabilities=capabilities)

    @server.tool()
    async def search_datarefs(query: str, limit: int = 30) -> DatarefSearchResult:
        """Search X-Plane's ~10,000 datarefs by name substring.

        Every whitespace-separated term must appear in the name — so
        ``airspeed pilot`` finds ``sim/cockpit2/gauges/indicators/
        airspeed_kts_pilot``. Case-insensitive. Start broad, then narrow:
        the result says how many matched beyond the limit. Read values with
        ``read_dataref`` using the exact name returned here.
        """
        limit = max(1, min(limit, _MAX_RESULTS))
        with _tool_errors():
            catalogue = await get_client().datarefs()
        matches, total = _search(catalogue, query, limit)
        return DatarefSearchResult(
            matches=[
                DatarefMatch(
                    name=entry.name,
                    value_type=entry.value_type,
                    is_writable=entry.is_writable,
                )
                for entry in matches
            ],
            total_matches=total,
        )

    @server.tool()
    async def read_dataref(name: str, index: int | None = None) -> DatarefValue:
        """Read one dataref's current value by its exact name.

        ``index`` picks a single element of an array dataref; without it an
        array arrives whole. If the name is wrong the error says so — find
        the right one with ``search_datarefs``.
        """
        with _tool_errors():
            entry = await _resolve_dataref(name)
            client = get_client()
            try:
                raw = await client.dataref_value(entry, index)
            except XPlaneResponseError as exc:
                if exc.status_code != 404:
                    raise
                # A 404 on a known id means the id went stale — X-Plane was
                # restarted since the catalogue was cached. Refresh and retry
                # once; a second failure is real.
                entry = (await client.datarefs(refresh=True))[name]
                raw = await client.dataref_value(entry, index)
        return DatarefValue(
            name=name,
            value=decode_value(raw, entry.value_type),
            value_type=entry.value_type,
        )

    @server.tool()
    async def read_datarefs(names: list[str]) -> list[DatarefValue]:
        """Read several datarefs in one call — one snapshot of related state.

        Prefer this over repeated ``read_dataref`` calls when building a
        picture (say, the whole autopilot state) so the values are close in
        time. Each name must be exact.
        """
        results: list[DatarefValue] = []
        with _tool_errors():
            client = get_client()
            for name in names:
                entry = await _resolve_dataref(name)
                raw = await client.dataref_value(entry)
                results.append(
                    DatarefValue(
                        name=name,
                        value=decode_value(raw, entry.value_type),
                        value_type=entry.value_type,
                    )
                )
        return results

    @server.tool()
    async def search_commands(query: str, limit: int = 30) -> CommandSearchResult:
        """Search X-Plane's commands by name or description substring.

        Commands are momentary actions — gear toggles, autopilot buttons,
        view changes — as distinct from datarefs, which are state. Fire one
        with ``execute_command`` using the exact name returned here.
        """
        limit = max(1, min(limit, _MAX_RESULTS))
        with _tool_errors():
            catalogue = await get_client().commands()
        matches, total = _search(catalogue, query, limit)
        return CommandSearchResult(
            matches=[
                CommandMatch(name=entry.name, description=entry.description) for entry in matches
            ],
            total_matches=total,
        )

    @server.tool()
    async def execute_command(name: str, duration: float = 0.0) -> CommandResult:
        """Activate an X-Plane command by its exact name.

        ``duration`` is how long the command is held in seconds, 0 to 10: 0
        means a plain press-and-release, which is right for toggles; a held
        duration is for commands that act while held (trim, brakes). This
        acts on a real simulator — prefer confirming intent with the user
        before firing anything they did not ask for.
        """
        if not 0.0 <= duration <= 10.0:
            raise ToolError("duration must be between 0 and 10 seconds.")
        with _tool_errors():
            client = get_client()
            entry = (await client.commands()).get(name)
            if entry is None:
                entry = (await client.commands(refresh=True)).get(name)
            if entry is None:
                raise ToolError(
                    f"No command is named '{name}' (exact match, case-sensitive). "
                    f"Use search_commands to find the right name."
                )
            try:
                await client.activate_command(entry, duration)
            except XPlaneResponseError as exc:
                if exc.status_code != 404:
                    raise
                entry = (await client.commands(refresh=True))[name]
                await client.activate_command(entry, duration)
        return CommandResult(name=name, activated=True, duration=duration)
