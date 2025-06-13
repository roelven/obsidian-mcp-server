from __future__ import annotations

import pytest

from tests.test_list_resources_pagination import _ensure_stubbed_mcp  # reuse helper

from obsidian_mcp_server.config import Settings


@pytest.mark.asyncio
async def test_list_tools_pagination(monkeypatch):
    """Validate cursor pagination over tools/list."""

    _ensure_stubbed_mcp(monkeypatch)

    from obsidian_mcp_server.server import ObsidianMCPServer  # reloaded stubbed

    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )

    server = ObsidianMCPServer(settings)

    # Access handler directly via stubs
    list_handler = server.app._method_handlers["tools/list"]  # type: ignore[index]

    first = await list_handler(limit=2)
    assert len(first["tools"]) == 2
    assert first["nextCursor"] is not None

    second = await list_handler(cursor=first["nextCursor"], limit=2)
    assert len(second["tools"]) >= 1  # remaining tools
    assert second["nextCursor"] is None

    import sys
    sys.modules.pop("obsidian_mcp_server.server", None) 