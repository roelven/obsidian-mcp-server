from __future__ import annotations

import pytest

from obsidian_mcp_server.server import ObsidianMCPServer
from obsidian_mcp_server.config import Settings


@pytest.fixture(scope="module")
def server() -> ObsidianMCPServer:  # type: ignore[return-type]
    """Create an ObsidianMCPServer instance with dummy settings for testing."""
    dummy_settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="vault",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="test-key",
        vault_id="vault123",
    )
    return ObsidianMCPServer(dummy_settings)


def test_uri_roundtrip(server: ObsidianMCPServer):
    """Verify that _create_note_uri and _extract_path_from_uri are symmetrical."""
    original_path = "notes/project-alpha/index.md"
    uri = server._create_note_uri(original_path)
    extracted = server._extract_path_from_uri(uri)
    assert extracted == original_path


# @pytest.mark.asyncio
# async def test_find_notes_exists_only(monkeypatch, server: ObsidianMCPServer):
#     """Ensure find_notes with exists_only returns correct JSON payload."""
#     sample_note = ObsidianNote(
#         path="sample.md",
#         title="Sample Note",
#         content="# Sample\ncontent",
#         created_at=0,
#         modified_at=0,
#         size=10,
#         tags=["demo"],
#     )
#
#     async def fake_search_notes(query: str, limit: int = 50):  # pylint: disable=unused-argument
#         return [(sample_note, 10.0)]
#
#     # Patch the couchdb_client.search_notes method
#     monkeypatch.setattr(server.couchdb_client, "search_notes", fake_search_notes)
#
#     # Call the tool via the handler
#     payload = {"query": "Sample", "exists_only": True}
#     # Retrieve the dynamically registered call_tool handler
#     call_tool_handler = server.app._method_handlers["tools/call"]  # type: ignore[attr-defined]
#     result: List = await call_tool_handler("find_notes", payload)  # type: ignore[func-returns-value]
#
#     assert isinstance(result, list) and result, "Expected non-empty list from handler"
#     content = json.loads(result[0].text)  # type: ignore[attr-defined]
#     assert content["exists"] is True
#     assert content["match_count"] == 1 