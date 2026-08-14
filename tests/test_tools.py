"""The whole tool surface, exercised through a real MCP client."""

from __future__ import annotations

import json

import pytest

import xplane_dataref_mcp.client as client_module
from tests.conftest import XPLANE_URL, ClientFactory, FakeXPlane
from xplane_dataref_mcp.client import XPlaneClient, get_client

EXPECTED_TOOLS = {
    "get_connection_status",
    "search_datarefs",
    "read_dataref",
    "read_datarefs",
    "search_commands",
    "execute_command",
}


async def test_every_tool_is_advertised(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        listing = await client.list_tools()
    assert {tool.name for tool in listing.tools} == EXPECTED_TOOLS


async def test_every_tool_describes_itself(mcp_client: ClientFactory) -> None:
    """A tool with no description is a tool a model calls at random."""
    async with mcp_client() as client:
        listing = await client.list_tools()
    for tool in listing.tools:
        assert tool.description, tool.name


async def test_every_tool_declares_an_output_schema(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        listing = await client.list_tools()
    for tool in listing.tools:
        assert tool.output_schema, tool.name


# ---------------------------------------------------------------------------
# Connection status
# ---------------------------------------------------------------------------


async def test_connection_status_reports_the_simulator(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool("get_connection_status")
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["reachable"] is True
    assert result.structured_content["url"] == XPLANE_URL
    assert result.structured_content["capabilities"]["api"]["versions"] == ["v1", "v2"]


async def test_an_absent_simulator_is_an_answer_not_an_error(
    sim: FakeXPlane, mcp_client: ClientFactory
) -> None:
    """This is the one tool where an unreachable X-Plane must not fail."""
    sim.refuse("GET", "/api/capabilities")
    async with mcp_client() as client:
        result = await client.call_tool("get_connection_status")
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["reachable"] is False
    detail = result.structured_content["detail"]
    assert XPLANE_URL in detail and "Web API" in detail


async def test_other_tools_fail_with_the_fix_in_the_sentence(
    sim: FakeXPlane, mcp_client: ClientFactory
) -> None:
    sim.refuse("GET", "/api/v2/datarefs")
    async with mcp_client() as client:
        result = await client.call_tool("search_datarefs", {"query": "airspeed"})
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert XPLANE_URL in text and "Accept incoming connections" in text


# ---------------------------------------------------------------------------
# Dataref search
# ---------------------------------------------------------------------------


async def test_search_is_an_and_of_terms(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool("search_datarefs", {"query": "airspeed pilot"})
    assert not result.is_error
    assert result.structured_content is not None
    names = [match["name"] for match in result.structured_content["matches"]]
    # "pilot" is a substring of "copilot", so both match — the shorter,
    # more-generic name must sort first.
    assert names == [
        "sim/cockpit2/gauges/indicators/airspeed_kts_pilot",
        "sim/cockpit2/gauges/indicators/airspeed_kts_copilot",
    ]
    assert result.structured_content["total_matches"] == 2


async def test_search_reports_truncation(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool("search_datarefs", {"query": "sim", "limit": 2})
    assert result.structured_content is not None
    assert len(result.structured_content["matches"]) == 2
    assert result.structured_content["total_matches"] == 5


async def test_search_is_case_insensitive(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool("search_datarefs", {"query": "TAILNUM"})
    assert result.structured_content is not None
    assert result.structured_content["total_matches"] == 1


async def test_the_catalogue_is_downloaded_once(sim: FakeXPlane, mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        await client.call_tool("search_datarefs", {"query": "airspeed"})
        await client.call_tool("search_datarefs", {"query": "latitude"})
    catalogue_requests = [
        request for request in sim.requests if request.url.path == "/api/v2/datarefs"
    ]
    assert len(catalogue_requests) == 1


async def test_the_catalogue_download_paginates(
    sim: FakeXPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a two-row page, five datarefs take three pages — and all arrive."""
    monkeypatch.setattr(client_module, "_PAGE", 2)
    catalogue = await get_client().datarefs()
    assert len(catalogue) == 5
    starts = [
        request.url.params["start"]
        for request in sim.requests
        if request.url.path == "/api/v2/datarefs"
    ]
    assert starts == ["0", "2", "4"]


async def test_a_server_that_ignores_pagination_does_not_hang(
    sim: FakeXPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same full page forever must terminate, not loop."""
    monkeypatch.setattr(client_module, "_PAGE", 5)
    sim.respond("GET", "/api/v2/datarefs", json={"data": sim.datarefs})
    catalogue = await get_client().datarefs()
    assert len(catalogue) == 5


# ---------------------------------------------------------------------------
# Reading values
# ---------------------------------------------------------------------------


async def test_read_a_float(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool(
            "read_dataref", {"name": "sim/cockpit2/gauges/indicators/airspeed_kts_pilot"}
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["value"] == 123.5
    assert result.structured_content["value_type"] == "float"


async def test_read_a_data_dataref_decodes_to_text(mcp_client: ClientFactory) -> None:
    """The wire carries base64-encoded NUL-padded bytes; the model gets text."""
    async with mcp_client() as client:
        result = await client.call_tool("read_dataref", {"name": "sim/aircraft/view/acf_tailnum"})
    assert result.structured_content is not None
    assert result.structured_content["value"] == "EC-ABC"


async def test_read_an_array_element_by_index(sim: FakeXPlane, mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool(
            "read_dataref",
            {"name": "sim/cockpit2/engine/indicators/N1_percent", "index": 1},
        )
    assert result.structured_content is not None
    assert result.structured_content["value"] == 94.8
    assert sim.last_request.url.params["index"] == "1"


async def test_an_unknown_name_points_at_search(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool("read_dataref", {"name": "sim/no/such/thing"})
    assert result.is_error
    assert "search_datarefs" in result.content[0].text  # type: ignore[union-attr]


async def test_a_stale_id_refreshes_and_retries(sim: FakeXPlane, mcp_client: ClientFactory) -> None:
    """X-Plane restarted mid-session: ids reshuffle, names survive.

    The first read 404s on the cached id; the tool must re-download the
    catalogue and succeed with the new id, invisibly to the caller.
    """
    name = "sim/flightmodel/position/latitude"
    async with mcp_client() as client:
        await client.call_tool("search_datarefs", {"query": "latitude"})  # warm the cache
        for row in sim.datarefs:
            if row["name"] == name:
                row["id"] = 103
        sim.values[103] = sim.values.pop(3)
        result = await client.call_tool("read_dataref", {"name": name})
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["value"] == 40.472


async def test_read_several_in_one_call(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool(
            "read_datarefs",
            {
                "names": [
                    "sim/flightmodel/position/latitude",
                    "sim/aircraft/view/acf_tailnum",
                ]
            },
        )
    assert not result.is_error
    assert result.structured_content is not None
    values = {row["name"]: row["value"] for row in result.structured_content["result"]}
    assert values == {
        "sim/flightmodel/position/latitude": 40.472,
        "sim/aircraft/view/acf_tailnum": "EC-ABC",
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def test_search_commands_matches_descriptions(mcp_client: ClientFactory) -> None:
    """'gear' appears only in the description's subject, not just the name."""
    async with mcp_client() as client:
        result = await client.call_tool("search_commands", {"query": "landing gear"})
    assert result.structured_content is not None
    assert [match["name"] for match in result.structured_content["matches"]] == [
        "sim/flight_controls/landing_gear_toggle"
    ]


async def test_execute_a_command_sends_the_duration(
    sim: FakeXPlane, mcp_client: ClientFactory
) -> None:
    async with mcp_client() as client:
        result = await client.call_tool(
            "execute_command",
            {"name": "sim/flight_controls/landing_gear_toggle", "duration": 0.5},
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["activated"] is True
    activation = sim.last_request
    assert activation.url.path == "/api/v2/command/10/activate"
    assert json.loads(activation.content) == {"duration": 0.5}


@pytest.mark.parametrize("duration", [-1.0, 10.5])
async def test_a_duration_out_of_range_never_reaches_the_wire(
    sim: FakeXPlane, mcp_client: ClientFactory, duration: float
) -> None:
    async with mcp_client() as client:
        result = await client.call_tool(
            "execute_command",
            {"name": "sim/flight_controls/landing_gear_toggle", "duration": duration},
        )
    assert result.is_error
    assert "between 0 and 10" in result.content[0].text  # type: ignore[union-attr]
    assert all("/activate" not in request.url.path for request in sim.requests)


async def test_an_unknown_command_points_at_search(mcp_client: ClientFactory) -> None:
    async with mcp_client() as client:
        result = await client.call_tool("execute_command", {"name": "sim/no/such/command"})
    assert result.is_error
    assert "search_commands" in result.content[0].text  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Client plumbing that no tool test reaches
# ---------------------------------------------------------------------------


async def test_a_malformed_catalogue_row_is_dropped_not_fatal(sim: FakeXPlane) -> None:
    sim.datarefs.append({"name": "missing-an-id"})
    catalogue = await get_client().datarefs()
    assert len(catalogue) == 5


async def test_the_error_sentence_survives_odd_bodies() -> None:
    """A proxy's HTML 502 must still produce a non-empty message."""
    import httpx

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    client = XPlaneClient("http://odd.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(client_module.XPlaneResponseError) as excinfo:
            await client.capabilities()
        assert "Bad Gateway" in str(excinfo.value)
    finally:
        await client.aclose()
