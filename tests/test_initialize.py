# noqa: D401 – docstring style irrelevant in tests
import sys
import importlib

import pytest


# Remove the lightweight stubs that are pre-registered by our test suite's
# `conftest.py` so we can import the *real* MCP SDK that we installed in the
# project's virtualenv (v1.9.4).
for mod_name in list(sys.modules):
    if mod_name == "mcp" or mod_name.startswith("mcp."):
        sys.modules.pop(mod_name)

# Also remove the lightweight pydantic stubs injected by conftest so the real
# `pydantic` and `pydantic_settings` libraries can be imported (they are
# available in the project dependencies).
for stub in [name for name in list(sys.modules) if name.startswith("pydantic") or name.startswith("pydantic_settings") or name.startswith("httpx") or name == "click"]:
    sys.modules.pop(stub)

# Import real packages back into sys.modules so subsequent tests work regardless
import importlib  # noqa: E402 – after sys.modules manipulation

for pkg in ("pydantic", "pydantic_settings", "httpx", "click"):
    try:
        importlib.import_module(pkg)
    except ModuleNotFoundError:
        # Real package may not be installed (e.g., httpx in slim env) – that's fine.
        pass

# Re-import the actual SDK modules now that the stubs are gone
import mcp.types as types  # type: ignore  # noqa: E402
from mcp.server.lowlevel import Server  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():  # noqa: D401 – fixture
    """Limit anyio tests to the asyncio backend to avoid requiring trio."""
    return "asyncio"


from obsidian_mcp_server.config import Settings  # noqa: E402
from obsidian_mcp_server.server import ObsidianMCPServer  # noqa: E402


@pytest.mark.anyio
async def test_create_initialization_options_basic():
    """The server should expose resources & tools capabilities in its initialization options."""

    # Provide minimal, dummy settings so the Settings model validates.
    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )

    server = ObsidianMCPServer(settings)

    # Re-apply strict version monkey-patch in case other tests reloaded the SDK
    import obsidian_mcp_server.server as _srv_mod  # noqa: E402
    _srv_mod._patch_strict_version_validation()

    # The real low-level Server instance is buried under the patched `.app` after
    # we reloaded the SDK above.  Assert the attribute exists before proceeding.
    assert hasattr(server.app, "create_initialization_options")

    init_opts = server.app.create_initialization_options()

    # Basic sanity checks
    assert init_opts.server_name == "obsidian-mcp-server"
    assert init_opts.server_version  # non-empty

    caps = init_opts.capabilities
    # The minimal server should at least advertise resources and tools
    assert caps.resources is not None, "Server must advertise resources capability"
    assert caps.tools is not None, "Server must advertise tools capability"

    # Protocol version is conveyed later in InitializeResult; here we just ensure constants align
    assert types.LATEST_PROTOCOL_VERSION == "2025-03-26"


@pytest.mark.anyio
async def test_initialize_version_mismatch(anyio_backend):  # noqa: D401
    """Server must reject unsupported protocol versions with JSON-RPC error −32001."""

    import anyio
    from mcp.shared.session import RequestResponder  # imported *after* stubs purge above
    from mcp.shared.message import SessionMessage

    # Minimal settings for server instantiation
    settings = Settings(
        couchdb_base_url="http://localhost:5984",
        couchdb_database_name="dummy",
        couchdb_user="user",
        couchdb_password="pass",
        api_key="dummy-key",
    )

    srv = ObsidianMCPServer(settings)

    # ---------------------------------------------------------------------
    # Build in-memory streams that mimic a transport layer
    # ---------------------------------------------------------------------
    # Use a *buffered* stream (capacity 1) so that ``session._send_response``
    # does not block waiting for the reader coroutine.  Otherwise the test
    # dead-locks on the unbuffered queue.
    client_to_server_send, server_read = anyio.create_memory_object_stream(1)
    server_write, client_read = anyio.create_memory_object_stream(1)

    # Initialise strict session (monkey-patched at import time)
    from mcp.server.lowlevel.server import (  # type: ignore  # noqa: WPS433, E402
        ServerSession as StrictSession,
    )

    session = StrictSession(
        server_read,
        server_write,
        init_options=srv.app.create_initialization_options(),
    )

    # Build an initialize request with an obviously unsupported version
    bad_version = "1900-01-01"
    init_request = types.ClientRequest(
        types.InitializeRequest(
            method="initialize",
            params=types.InitializeRequestParams(
                protocolVersion=bad_version,
                capabilities=types.ClientCapabilities(),
                clientInfo=types.Implementation(name="test-client", version="0.0.1"),
            ),
        )
    )

    responder = RequestResponder(
        request_id=1,
        request_meta=None,
        request=init_request,
        session=session,
        on_complete=lambda _: None,
    )

    # Invoke the patched handler
    await session._received_request(responder)  # type: ignore[misc]

    # Receive the server's error response
    session_message: SessionMessage = await client_read.receive()
    json_root = session_message.message.root

    assert isinstance(json_root, types.JSONRPCError)
    assert json_root.error.code == -32001 