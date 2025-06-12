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
for stub in [name for name in sys.modules if name.startswith("pydantic") or name.startswith("pydantic_settings") or name.startswith("httpx") or name == "click"]:
    sys.modules.pop(stub)

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