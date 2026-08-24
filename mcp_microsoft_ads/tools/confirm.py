from .. import rails
from ..app import mcp


@mcp.tool()
def confirm_and_apply(draft_id: str) -> dict:
    """Execute a previously drafted write. The ONLY path that mutates the account.

    Live-verified from 2026-07-28 onward — the only mutation path in this server;
    every live write in the project flowed through it."""
    return rails.apply_draft(draft_id)
