from __future__ import annotations

import pytest

from obsidian_mcp_server.pagination import (
    encode_cursor,
    decode_cursor,
    validate_limit,
    MAX_LIMIT,
    CursorError,
)


def test_cursor_roundtrip() -> None:
    """Encoding then decoding a payload should return the original value."""
    payload = {"last": 1234567890, "offset": 10}
    token = encode_cursor(payload)
    assert isinstance(token, str) and token, "encode_cursor must return non-empty str"
    decoded = decode_cursor(token)
    assert decoded == payload


@pytest.mark.parametrize("bad_token", ["", "%%%", "!!!", "YWJjZA", "12345" "invalid==token"])
def test_cursor_decode_errors(bad_token: str) -> None:
    """Malformed tokens raise ``CursorError``."""
    with pytest.raises(CursorError):
        decode_cursor(bad_token)  # type: ignore[arg-type]


@pytest.mark.parametrize("value,expected", [(None, 20), (1, 1), (MAX_LIMIT, MAX_LIMIT)])
def test_validate_limit_ok(value, expected):
    assert validate_limit(value, default=20) == expected


@pytest.mark.parametrize("value", [0, -1, MAX_LIMIT + 1, 3.14, "ten"])
def test_validate_limit_errors(value):
    with pytest.raises(ValueError):
        validate_limit(value, default=20) 