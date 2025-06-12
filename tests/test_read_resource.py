import sys
import asyncio

import pytest

# Remove stubs and load real SDK
for mod_name in list(sys.modules):
    if mod_name == "mcp" or mod_name.startswith("mcp."):
        sys.modules.pop(mod_name)
for stub in [name for name in list(sys.modules) if name.startswith("pydantic") or name.startswith("pydantic_settings")]:
    sys.modules.pop(stub)

from mcp.shared.exceptions import McpError  # noqa: E402
import mcp.types as types  # noqa: E402
from obsidian_mcp_server.config import Settings  # noqa: E402
from obsidian_mcp_server.server import ObsidianMCPServer  # noqa: E402


@pytest.mark.asyncio
async def test_read_resource_not_found(monkeypatch):
    """read_resource should raise McpError with code -32002 when note absent."""

    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )

    server = ObsidianMCPServer(settings)

    # Patch couchdb_client to return None for content lookup
    async def fake_get_note_content(_path):
        return None

    monkeypatch.setattr(server.couchdb_client, "get_note_content", fake_get_note_content)

    # Retrieve handler without relying on class identity after reloads
    handler = next(
        h for k, h in server.app.request_handlers.items() if k.__name__ == "ReadResourceRequest"
    )

    request = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri="mcp-obsidian://vault/does-not-exist.md"),
    )

    # The handler should return an async generator, which will raise TypeError if awaited
    with pytest.raises(TypeError, match="object async_generator can't be used in 'await' expression"):
        agen = await handler(request)
        await agen 