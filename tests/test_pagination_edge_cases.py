from __future__ import annotations

import pytest

from tests.test_list_resources_pagination import _ensure_stubbed_mcp
from obsidian_mcp_server.config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_key", ["resources/list", "tools/list"])
async def test_invalid_limit_and_cursor(monkeypatch, handler_key):
    """Supplying bad cursor or oversized limit should raise McpError."""

    _ensure_stubbed_mcp(monkeypatch)
    from obsidian_mcp_server.server import ObsidianMCPServer, McpError  # reloaded stubbed

    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )

    server = ObsidianMCPServer(settings)
    handler = server.app._method_handlers[handler_key]  # type: ignore[index]

    # Invalid cursor
    with pytest.raises(McpError):
        await handler(cursor="!!!", limit=5)

    # Excessive limit (>50)
    with pytest.raises(McpError):
        await handler(limit=100)

    import sys
    sys.modules.pop("obsidian_mcp_server.server", None) 