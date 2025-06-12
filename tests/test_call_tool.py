import sys
from types import SimpleNamespace

import pytest

# Purge any stubbed MCP modules injected by tests/conftest so that we can
# import the *real* SDK.  We will then import `obsidian_mcp_server.server`,
# which re-applies the strict-version monkey-patch.

for mod_name in list(sys.modules):
    if mod_name == "mcp" or mod_name.startswith("mcp."):
        sys.modules.pop(mod_name)

# Remove lightweight stubs so real libs are imported
for stub in [name for name in list(sys.modules) if name.startswith("pydantic") or name.startswith("pydantic_settings") or name == "click" or name.startswith("httpx")]:
    sys.modules.pop(stub)

# Import server first so the monkey-patch executes before we touch the SDK
from obsidian_mcp_server.server import ObsidianMCPServer  # noqa: E402
import mcp.types as types  # noqa: E402
from obsidian_mcp_server.config import Settings  # noqa: E402


@pytest.mark.asyncio
async def test_call_tool_find_notes(monkeypatch):
    """`find_notes` should return CallToolResult with isError=False."""

    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )
    server = ObsidianMCPServer(settings)

    # Patch the CouchDB client so no real DB access occurs
    monkeypatch.setattr(
        server,
        "couchdb_client",
        SimpleNamespace(
            list_notes=lambda *a, **kw: [],
            process_note=lambda *a, **kw: None,
        ),
    )

    handler = server.app.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="find_notes", arguments={}),
    )

    result: types.ServerResult = await handler(request)
    assert isinstance(result.root, types.CallToolResult)
    assert result.root.isError is False


@pytest.mark.asyncio
async def test_call_tool_unknown(monkeypatch):
    """Unknown tool should yield isError=True."""

    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )
    server = ObsidianMCPServer(settings)

    handler = server.app.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="does_not_exist", arguments={}),
    )

    result: types.ServerResult = await handler(request)
    assert isinstance(result.root, types.CallToolResult)
    assert result.root.isError is True 