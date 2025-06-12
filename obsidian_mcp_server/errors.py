from __future__ import annotations

"""Utility functions for creating structured MCP JSON-RPC error objects.

These helpers centralise our server-side error codes so handlers can raise
``mcp.shared.exceptions.McpError`` with consistent payloads.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel

# Temporary ErrorData implementation until we fix MCP imports
class ErrorData(BaseModel):
    """JSON-RPC error object data."""
    code: int
    message: str
    data: Optional[Dict[str, Any]] = None

# ---------------------------------------------------------------------------
# Custom server error codes (range −32000 to −32099 is "Server error" in JSON-RPC)
# ---------------------------------------------------------------------------

PROTOCOL_VERSION_MISMATCH = -32001
RESOURCE_NOT_FOUND = -32002
RATE_LIMIT_EXCEEDED = -32003
INTERNAL_SERVER_ERROR = -32099  # fallback


def resource_not_found(uri: str) -> ErrorData:  # pragma: no cover
    """Return ErrorData for a missing resource."""

    return ErrorData(code=RESOURCE_NOT_FOUND, message="Resource not found", data={"uri": uri})


def rate_limit_exceeded() -> ErrorData:  # pragma: no cover
    """Return ErrorData for rate-limit violations."""

    return ErrorData(code=RATE_LIMIT_EXCEEDED, message="Rate limit exceeded")


def internal_error(msg: str) -> ErrorData:  # pragma: no cover
    """Generic catch-all internal error helper."""

    return ErrorData(code=INTERNAL_SERVER_ERROR, message=msg) 