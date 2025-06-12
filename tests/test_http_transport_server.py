"""Test the streamable HTTP transport."""

import json
import logging
import pytest
import asyncio
import httpx
from obsidian_mcp_server.server import ObsidianMCPServer
from obsidian_mcp_server.config import Settings
from starlette.applications import Starlette

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@pytest.fixture
def app():
    """Create a test app."""
    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="test_db",
        couchdb_user="test_user",
        couchdb_password="test_pass",
        api_key="test_key"
    )
    server = ObsidianMCPServer(settings)
    app = server.build_http_app()

    yield app

    # Clean up
    async def cleanup():
        await server.close()

    # Create a new event loop for cleanup
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(cleanup())
    finally:
        loop.close()

@pytest.mark.timeout(5)  # 5 second timeout for the entire test
@pytest.mark.asyncio
async def test_streamable_http_ping_roundtrip(app):
    """Test a complete ping round-trip using the streamable HTTP transport (fully async)."""
    logger.debug("Starting ping round-trip test")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Send ping request
        payload = {"jsonrpc": "2.0", "id": 42, "method": "ping"}
        logger.debug(f"Sending ping request: {payload}")
        response = await asyncio.wait_for(client.post("/messages/", json=payload), timeout=2.0)
        assert response.status_code == 202
        session_id = response.headers["Mcp-Session-Id"]
        logger.debug(f"Got session ID: {session_id}")

        try:
            # Open SSE connection and wait for message
            logger.debug(f"Opening SSE connection for session {session_id}")
            async with client.stream("GET", "/messages/", headers={"Mcp-Session-Id": session_id}) as sse_response:
                assert sse_response.status_code == 200
                try:
                    # Create a task to read SSE messages
                    async def read_sse_messages():
                        logger.debug("Starting to read SSE messages")
                        async for line in sse_response.aiter_lines():
                            logger.debug(f"SSE line: {line}")
                            if line.startswith("data: "):
                                data = json.loads(line[6:])
                                logger.debug(f"Parsed SSE message: {data}")
                                # Verify response
                                assert data["jsonrpc"] == "2.0"
                                assert data["id"] == 42
                                assert "result" in data
                                assert data["result"] == "pong"
                                return data

                    # Wait for SSE message with timeout
                    logger.debug("Waiting for SSE message with timeout")
                    data = await asyncio.wait_for(read_sse_messages(), timeout=2.0)
                    logger.debug(f"Received SSE message: {data}")
                except asyncio.TimeoutError:
                    logger.error("Timeout waiting for SSE message")
                    pytest.fail("Timeout waiting for SSE message")
        except Exception as e:
            logger.error(f"Error in test: {e}")
            pytest.fail(f"Test failed: {e}")
        finally:
            # Clean up the session
            from obsidian_mcp_server.transport.http_stream import _session_mgr
            await _session_mgr.cleanup_session(session_id)
