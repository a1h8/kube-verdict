"""
End-to-end test of the MCP server over its real stdio transport.

Spawns `python mcp_server.py` as a subprocess and drives it with the MCP SDK
client — the same path Claude Desktop / Cursor use. Exercises `blast_radius`,
which needs neither a cluster nor an LLM, so the round-trip is hermetic.

This is the missing link between "unit tests pass" and "the server actually
boots and answers on stdio".
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

_REPO_ROOT = Path(__file__).parent.parent.parent

pytestmark = pytest.mark.asyncio


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["mcp_server.py"],
        cwd=str(_REPO_ROOT),
    )


async def test_list_tools_over_stdio():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
    names = {t.name for t in result.tools}
    assert names == {"kube_rca", "helm_drift", "expected_state_drift", "blast_radius"}


async def test_blast_radius_round_trip():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "blast_radius",
                {"remediation_commands": ["kubectl delete pod api-0 -n prod"]},
                read_timeout_seconds=timedelta(seconds=30),
            )
    assert not result.isError
    payload = json.loads(result.content[0].text)
    # compute_blast_radius shape — a real assessment came back through stdio.
    assert "risk" in payload
    assert payload["risk"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


async def test_unknown_tool_is_error_over_stdio():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("does_not_exist", {})
    assert result.isError is True
