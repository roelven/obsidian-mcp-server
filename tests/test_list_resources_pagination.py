from __future__ import annotations

import pytest

# Recreate stubbed MCP environment so we can access the decorator-registered
# ``_method_handlers`` attribute on the fake Server instance (easier than JSON-RPC).

from obsidian_mcp_server.config import Settings  # noqa: E402
from obsidian_mcp_server.types import ObsidianNote  # noqa: E402


def _ensure_stubbed_mcp(monkeypatch):
    """(Re)install the lightweight MCP stubs from tests.conftest."""
    from importlib import reload
    import sys
    from tests import conftest as _ct  # type: ignore

    # Re-register stubs (they might have been removed by other tests)
    _ct._create_mcp_stub()  # type: ignore[attr-defined]

    # Reload server module so decorator registration attaches to the stubbed Server
    import obsidian_mcp_server.server as _srv_mod  # noqa: E402

    reload(_srv_mod)

    # Monkeypatch the module reference used below so we get the reloaded version
    monkeypatch.setitem(sys.modules, "obsidian_mcp_server.server", _srv_mod)


@pytest.mark.asyncio
async def test_list_resources_pagination(monkeypatch):
    """First call returns a nextCursor; second call with that cursor yields next page."""

    # ------------------------------------------------------------------
    # Ensure we are operating against the stubbed MCP SDK so we can access
    # ``_method_handlers``.
    # ------------------------------------------------------------------

    _ensure_stubbed_mcp(monkeypatch)

    from obsidian_mcp_server.server import ObsidianMCPServer  # type: ignore  # noqa: E402

    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )

    server = ObsidianMCPServer(settings)

    # ------------------------------------------------------------------
    # Prepare mock CouchDB responses
    # ------------------------------------------------------------------
    total_notes = 6
    entries = list(range(total_notes))  # dummy opaque entries

    async def fake_list_notes(limit: int = 100, skip: int = 0, **_kw):  # noqa: D401
        return entries[skip : skip + limit]

    async def fake_process_note(entry):  # noqa: D401 – minimal conversion
        return ObsidianNote(
            path=f"note{entry}.md",
            title=f"Note {entry}",
            content="",
            created_at=0,
            modified_at=entry,
            size=10,
        )

    monkeypatch.setattr(server.couchdb_client, "list_notes", fake_list_notes)
    monkeypatch.setattr(server.couchdb_client, "process_note", fake_process_note)

    # Retrieve bare handler (decorator registered function)
    list_handler = server.app._method_handlers["resources/list"]  # type: ignore[index]

    # ------------------------------------------------------------------
    # Page 1 – request 3 items
    # ------------------------------------------------------------------
    first_page = await list_handler(limit=3)
    assert len(first_page["resources"]) == 3
    assert first_page["nextCursor"] is not None

    # ------------------------------------------------------------------
    # Page 2 – using returned cursor
    # ------------------------------------------------------------------
    second_page = await list_handler(cursor=first_page["nextCursor"], limit=3)
    assert len(second_page["resources"]) == 3
    # No more data afterwards
    assert second_page["nextCursor"] is None

    # ------------------------------------------------------------------
    # Clean up: remove stubbed server module so subsequent tests can import
    # the real SDK without interference.
    # ------------------------------------------------------------------
    import sys
    sys.modules.pop("obsidian_mcp_server.server", None) 