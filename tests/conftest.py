"""Test configuration and lightweight stubs so that the core package can be imported
without installing heavy, compiled, or unavailable third-party dependencies.

The production code depends on libraries such as `mcp`, `pydantic`, `pydantic_settings`,
`click`, `anyio`, `httpx`, and `frontmatter`.  For our small, self-contained unit
suite we only need a subset of functionality – essentially just enough for the
imports that happen while constructing an `ObsidianMCPServer` object.

Here we create *very* small shims and register them in `sys.modules` *before* the
package under test is imported.  This keeps the test environment fast and avoids
installation issues on CI machines where compiling wheels (for example
`pydantic-core` on Python 3.13) would otherwise fail.
"""

from __future__ import annotations

import sys
import types
from types import ModuleType
from typing import Any, Callable, List


# ---------------------------------------------------------------------------
# Helper to create and register a stub module hierarchy quickly
# ---------------------------------------------------------------------------

def _ensure_module(name: str) -> ModuleType:  # pragma: no cover – helper
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# ---------------------------------------------------------------------------
# Stub for the `pydantic` package – *very* trimmed down
# ---------------------------------------------------------------------------

pydantic = _ensure_module("pydantic")

# pylint: disable=too-few-public-methods,super-init-not-called
class _PseudoModel(dict):
    """Extremely small subset of the Pydantic v2 interface used in tests."""

    # Mapping of attribute aliases we want to resolve, mimicking `Field(alias=...)`
    _ALIASES = {
        "id": "_id",
        "rev": "_rev",
        "deleted": "_deleted",
    }

    def __init__(self, **data: Any):
        # Store data directly so attribute access works
        super().__init__(**data)

        # Populate alias keys so direct `.id` access (not going through __getattr__)
        for public_key, private_key in self._ALIASES.items():
            if private_key in self and public_key not in self:
                super().__setitem__(public_key, self[private_key])

    # ------------------------------------------------------------------
    # Attribute access helpers
    # ------------------------------------------------------------------

    def __getattribute__(self, item: str):  # noqa: D401 – type: ignore[override]
        # Prioritise dictionary items so they shadow class attributes generated
        # by our Field stubs.
        if item != "__dict__" and item in super().__getattribute__("keys")():
            return super().__getitem__(item)

        return super().__getattribute__(item)

    def __getattr__(self, item: str) -> Any:  # noqa: D401
        # Called only if normal attribute lookup fails – we fall back to the
        # internal dict and alias mapping.
        if item in self:
            return self[item]

        if item in self._ALIASES and self._ALIASES[item] in self:
            return self[self._ALIASES[item]]

        raise AttributeError(item)

    # Provide `.model_dump()` so code/tests calling it won't fail
    def model_dump(self) -> dict[str, Any]:  # noqa: D401 – simple passthrough
        return dict(self)


# Public aliases expected by the production code
pydantic.BaseModel = _PseudoModel  # type: ignore[attr-defined]
pydantic.Field = lambda *args, **kwargs: None  # type: ignore[attr-defined]
pydantic.AnyUrl = str  # type: ignore[attr-defined]


# v2 `@field_validator` decorator – becomes a no-op
pydantic.field_validator = (  # type: ignore[attr-defined]
    lambda *args, **kwargs: (lambda f: f)
)

# Register the module variants (`pydantic.v1` etc.) just in case
_ensure_module("pydantic.v1")


# ---------------------------------------------------------------------------
# Stub for `pydantic_settings`
# ---------------------------------------------------------------------------

pydantic_settings = _ensure_module("pydantic_settings")

class _BaseSettings(_PseudoModel):
    """Very small drop-in for `pydantic_settings.BaseSettings`."""

    # In real `BaseSettings`, env vars override defaults – we don't need that.

    def __init__(self, **kwargs):
        # Inject a couple of sensible defaults used by the server so tests
        # don't have to specify them explicitly.
        kwargs.setdefault("rate_limit_requests_per_minute", 60)
        kwargs.setdefault("rate_limit_burst_size", 10)
        super().__init__(**kwargs)

pydantic_settings.BaseSettings = _BaseSettings  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Stub for the `mcp` SDK
# ---------------------------------------------------------------------------

def _create_mcp_stub() -> None:  # pragma: no cover – helper
    mcp = _ensure_module("mcp")
    mcp.types = types.ModuleType("mcp.types")  # type: ignore[attr-defined]
    sys.modules["mcp.types"] = mcp.types

    # Extremely lightweight representations used only in tests
    class _Plain:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def __repr__(self):  # pragma: no cover – dev helper
            attrs = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
            return f"<{self.__class__.__name__} {attrs}>"

    for _cls_name in ("Resource", "Tool", "TextContent"):
        setattr(mcp.types, _cls_name, _Plain)

    # ------------------------------------------------------------------
    # Fake `mcp.server.lowlevel.Server`
    # ------------------------------------------------------------------

    mcp.server = types.ModuleType("mcp.server")  # type: ignore[attr-defined]
    sys.modules["mcp.server"] = mcp.server
    mcp.server.lowlevel = types.ModuleType("mcp.server.lowlevel")  # type: ignore[attr-defined]
    sys.modules["mcp.server.lowlevel"] = mcp.server.lowlevel

    class _FakeServer:  # pragma: no cover – methods are trivial
        def __init__(self, name: str):
            self.name = name
            self._method_handlers: dict[str, Callable] = {}

        # Decorator helpers mimic the real API signature
        def _register(self, key: str):
            def decorator(fn: Callable):
                self._method_handlers[key] = fn  # type: ignore[assignment]
                return fn

            return decorator

        def list_resources(self):
            return self._register("resources/list")

        def read_resource(self):
            return self._register("resources/read")

        def list_tools(self):
            return self._register("tools/list")

        def call_tool(self):
            return self._register("tools/call")

        # Stub for the runtime; tests don't actually call it
        async def run(self, *_args, **_kwargs):  # noqa: D401, D401 – unused
            raise RuntimeError("Not implemented in stub – shouldn't be called in unit tests")

    mcp.server.lowlevel.Server = _FakeServer  # type: ignore[attr-defined]


_create_mcp_stub()


# ---------------------------------------------------------------------------
# Lightweight stubs for assorted runtime-only deps (click, anyio, httpx, etc.)
# ---------------------------------------------------------------------------

for _name in ("click", "anyio"):
    _ensure_module(_name)

# Very small `httpx` stub – enough for creating an `AsyncClient`
# httpx = _ensure_module("httpx")

# class _FakeResponse:  # pragma: no cover
#     def __init__(self, status_code: int = 200, json_data: Any | None = None):
#         self.status_code = status_code
#         self._json = json_data or {}

#     def json(self):  # noqa: D401 – simple passthrough
#         return self._json

# class _FakeAsyncClient:  # pragma: no cover – only minimal async support
#     def __init__(self, *args, **kwargs):  # noqa: D401 – store nothing
#         pass

#     async def __aenter__(self):
#         return self

#     async def __aexit__(self, exc_type, exc_val, exc_tb):
#         pass

#     async def get(self, *_args, **_kwargs):  # noqa: D401 – always 200/empty
#         return _FakeResponse()

#     async def post(self, *_args, **_kwargs):  # noqa: D401 – always 200/empty
#         return _FakeResponse()

#     async def aclose(self):  # pragma: no cover
#         pass

# httpx.AsyncClient = _FakeAsyncClient  # type: ignore[attr-defined]

# Frontmatter stub – only the attribute is imported, not used directly in the tests
_ensure_module("frontmatter")


# ---------------------------------------------------------------------------
# Stub for the `cryptography` package – only what is imported in encryption.py
# ---------------------------------------------------------------------------

crypto = _ensure_module("cryptography")
crypto.hazmat = types.ModuleType("cryptography.hazmat")
sys.modules["cryptography.hazmat"] = crypto.hazmat
crypto.hazmat.primitives = types.ModuleType("cryptography.hazmat.primitives")
sys.modules["cryptography.hazmat.primitives"] = crypto.hazmat.primitives

# Sub-sub-modules referenced
_hashes_mod = types.ModuleType("cryptography.hazmat.primitives.hashes")
_serial_mod = types.ModuleType("cryptography.hazmat.primitives.serialization")
_kdf_mod = types.ModuleType("cryptography.hazmat.primitives.kdf")
_kdf_pbkdf2_mod = types.ModuleType("cryptography.hazmat.primitives.kdf.pbkdf2")
_cipher_mod = types.ModuleType("cryptography.hazmat.primitives.ciphers")
_cipher_aead_mod = types.ModuleType("cryptography.hazmat.primitives.ciphers.aead")

sys.modules[_hashes_mod.__name__] = _hashes_mod
sys.modules[_serial_mod.__name__] = _serial_mod
sys.modules[_kdf_mod.__name__] = _kdf_mod
sys.modules[_kdf_pbkdf2_mod.__name__] = _kdf_pbkdf2_mod
sys.modules[_cipher_mod.__name__] = _cipher_mod
sys.modules[_cipher_aead_mod.__name__] = _cipher_aead_mod

crypto.hazmat.primitives.hashes = _hashes_mod  # type: ignore[attr-defined]
crypto.hazmat.primitives.serialization = _serial_mod  # type: ignore[attr-defined]
crypto.hazmat.primitives.kdf = _kdf_mod  # type: ignore[attr-defined]
crypto.hazmat.primitives.ciphers = _cipher_mod  # type: ignore[attr-defined]

# Provide placeholder classes used in encryption.py
class _Dummy:
    def __init__(self, *args, **kwargs):
        pass

class _DummyAESGCM(_Dummy):
    def decrypt(self, *args, **kwargs):  # noqa: D401 – not actually used in tests
        raise RuntimeError("AESGCM.decrypt called in stub – this should not happen in unit tests")

aesgcm_class = _DummyAESGCM

_hashes_mod.hashes = _Dummy  # type: ignore[attr-defined]
_cipher_aead_mod.AESGCM = aesgcm_class  # type: ignore[attr-defined]
_kdf_pbkdf2_mod.PBKDF2HMAC = _Dummy  # type: ignore[attr-defined]

# Attach nested path so `from cryptography.hazmat.primitives.ciphers.aead import AESGCM` works
crypto.hazmat.primitives.ciphers.aead = _cipher_aead_mod  # type: ignore[attr-defined]
crypto.hazmat.primitives.kdf.pbkdf2 = _kdf_pbkdf2_mod  # type: ignore[attr-defined]

# Provide simple no-op decorators for click.command and click.option
click = sys.modules["click"]
if not hasattr(click, "command"):
    def _noop_decorator(*_dargs, **_dkwargs):  # noqa: D401 – decorator factory
        def wrapper(fn):
            return fn
        return wrapper

    click.command = _noop_decorator  # type: ignore[attr-defined]
    click.option = _noop_decorator  # type: ignore[attr-defined]

    class _Choice(list):
        def __init__(self, seq):
            super().__init__(seq)

    click.Choice = _Choice  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# End of stubs
# --------------------------------------------------------------------------- 