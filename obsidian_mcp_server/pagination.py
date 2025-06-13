import base64
import json
from typing import Any, Dict, Optional

__all__ = [
    "encode_cursor",
    "decode_cursor",
    "validate_limit",
    "MAX_LIMIT",
]

MAX_LIMIT: int = 50  # Hard cap enforced by the specification


class CursorError(ValueError):
    """Raised when a cursor cannot be decoded or is otherwise invalid."""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def encode_cursor(payload: Dict[str, Any]) -> str:
    """Return an opaque Base64-encoded cursor string for *payload*.

    The *payload* MUST be JSON-serialisable.  We purposefully use URL-safe Base64
    without padding so the resulting token can be transmitted in HTTP headers
    without additional encoding.
    """
    json_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    token = base64.urlsafe_b64encode(json_bytes).rstrip(b"=").decode()
    return token


def decode_cursor(token: str) -> Dict[str, Any]:
    """Decode *token* back into a Python dictionary.

    Raises ``CursorError`` if the token is malformed or cannot be decoded into
    a JSON object.
    """
    try:
        # Restore missing Base64 padding if required
        padding = "=" * (-len(token) % 4)
        json_bytes = base64.urlsafe_b64decode(token + padding)
        payload = json.loads(json_bytes)
        if not isinstance(payload, dict):  # pragma: no cover – defensive
            raise TypeError("Decoded cursor payload must be a JSON object")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError) as exc:  # noqa: D401
        raise CursorError("Invalid cursor token") from exc


def validate_limit(limit: Optional[int], default: int) -> int:
    """Return a *sanitised* ``limit`` value or raise ``ValueError``.

    The caller supplies a *default* that is used when *limit* is ``None``.
    """
    if limit is None:
        limit = default
    if not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit 