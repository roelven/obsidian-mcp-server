import json
import sys

# Ensure *real* httpx is used (remove any stubbed version loaded by other tests)
for mod in list(sys.modules):
    if mod.startswith("httpx"):
        sys.modules.pop(mod)

import pytest
import httpx

from obsidian_mcp_server.server import ObsidianMCPServer
from obsidian_mcp_server.config import Settings


@pytest.mark.anyio
async def test_streamable_http_ping_roundtrip():
    """End-to-end test: POST ping, GET SSE → pong result."""

    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="vault",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="test-key",
        vault_id="vault123",
    )

    server = ObsidianMCPServer(settings)
    app = server.build_http_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"jsonrpc": "2.0", "id": 42, "method": "ping"}
        resp = await client.post("/messages/", json=payload)
        assert resp.status_code == 202
        sid = resp.headers["Mcp-Session-Id"]

        # Open SSE stream and expect the raw message to be echoed back.
        async with client.stream("GET", "/messages/", headers={"Mcp-Session-Id": sid}) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data: "):
                    message = json.loads(line[6:])
                    # For slice 4.1, we expect the sent payload to be echoed back
                    assert message == payload
                    break


@pytest.fixture
def anyio_backend():
    # httpx.ASGITransport requires asyncio backend
    return "asyncio"
