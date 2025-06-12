import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

# Remove stubs so real httpx is used
import sys

for mod in list(sys.modules):
    if mod.startswith("httpx"):
        sys.modules.pop(mod)

import pytest
import httpx
from obsidian_mcp_server.server import ObsidianMCPServer
from obsidian_mcp_server.config import Settings


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    return Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="test_db",
        couchdb_user="test_user",
        couchdb_password="test_pass",
        api_key="test_key"
    )


@pytest.fixture
def mock_couchdb_client():
    """Create a mock CouchDB client."""
    client = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_http_stream_roundtrip(mock_settings, mock_couchdb_client):
    """POSTed message should be received via GET event-stream with same session id."""
    # Create server and build app
    server = ObsidianMCPServer(mock_settings)
    server.couchdb_client = mock_couchdb_client
    app = server.build_http_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        resp = await client.post("/messages/", json=payload)
        assert resp.status_code == 202
        sid = resp.headers["Mcp-Session-Id"]
        # Now open SSE stream
        async with client.stream("GET", "/messages/", headers={"Mcp-Session-Id": sid}) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    received = json.loads(line[6:])
                    assert received["jsonrpc"] == "2.0"
                    assert received["id"] == 1
                    assert received["result"] == "pong"
                    break

    # Clean up
    await server.close() 