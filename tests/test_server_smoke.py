"""Smoke: importing server registers every tool, and the one mutation path is wired.

Covers what unit tests structurally can't: tests import tool modules directly, so a
tool module dropped from server.py's import list (= silently missing over the wire,
exactly what a 47-tool MCP registration bug looks like) would break nothing else.
"""
import pytest

import mcp_microsoft_ads.server  # noqa: F401  — importing registers all tools
from mcp_microsoft_ads.app import mcp
from mcp_microsoft_ads.tools.confirm import confirm_and_apply


def test_all_47_tools_registered():
    tools = mcp._tool_manager._tools
    assert len(tools) == 47, f"expected 47 registered tools, got {len(tools)}: {sorted(tools)}"
    assert "confirm_and_apply" in tools


def test_confirm_and_apply_rejects_unknown_draft():
    # the actual @mcp.tool-decorated function, not rails.apply_draft directly
    with pytest.raises(ValueError, match="unknown or already-applied"):
        confirm_and_apply("no-such-draft")
