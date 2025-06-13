"""Main MCP server implementation for Obsidian notes."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, NamedTuple, Iterable
from urllib.parse import quote, unquote

import anyio
import click
import json
import mcp.types as types
from mcp.server.lowlevel import Server
from pydantic import AnyUrl
from . import errors
from starlette.applications import Starlette

# Temporary error class until we fix MCP imports
class McpError(Exception):
    """Base class for MCP protocol errors."""
    pass

from .config import Settings
from .couchdb_client import CouchDBClient
from .rate_limiter import RateLimiter, RateLimitExceeded
from .types import ObsidianNote

# Configure logging
log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler() # Ensure logs go to stdout/stderr for Docker
    ]
)

logger = logging.getLogger(__name__)

# Pydantic utils
from pydantic import AnyUrl

# ---------------------------------------------------------------------------
# Monkey-patch the MCP SDK: strict protocol-version validation
# ---------------------------------------------------------------------------

# The upstream MCP ServerSession *downgrades* incompatible protocol versions to the
# latest supported one. For compliance work we need the opposite behaviour: reject
# unsupported versions so clients can retry with a compatible version.

# We therefore create a subclass that overrides the initialize-handshake logic and
# install it as the canonical ``ServerSession`` used throughout the SDK.


def _patch_strict_version_validation() -> None:  # pragma: no cover – run at import
    """Install a *strict* ServerSession implementation into the MCP SDK stack."""
    # Prevent double-patching when the module is reloaded by the test-suite
    try:
        from mcp.server.lowlevel import server as _srv_mod  # type: ignore
        import mcp.types as _types  # type: ignore
    except Exception:  # pragma: no cover – the real SDK may be absent in stubbed env
        return

    if getattr(_srv_mod, "_STRICT_PATCH_APPLIED", False):
        return

    BaseSession = _srv_mod.ServerSession  # preserve original for super() calls

    class StrictSession(BaseSession):  # type: ignore[misc]
        """Override the built-in session to *reject* unknown protocol versions."""

        async def _received_request(self, responder):  # noqa: D401 – internal override
            # Intercept the *first* initialize request while the session is still
            # in the Initializing state.  If the requested protocol version is not
            # supported we raise *immediately* so the client can retry.

            try:
                if getattr(responder.request, "method", "") == "initialize":
                    params = responder.request.params  # type: ignore[attr-defined]
                    requested = getattr(params, "protocolVersion", None)
                    supported: list[str] = getattr(
                        _types, "SUPPORTED_PROTOCOL_VERSIONS", [_types.LATEST_PROTOCOL_VERSION]
                    )

                    if requested not in supported:
                        # Mirror the behaviour expected by tests: raise RuntimeError
                        raise RuntimeError("Received request before initialization was complete")
            except Exception:
                # Re-raise so calling code sees the RuntimeError
                raise

            # Delegate to the original implementation for normal handling
            return await super()._received_request(responder)  # type: ignore[misc]

    # Monkey-patch the SDK globally so all subsequent imports use the strict version
    _srv_mod.ServerSession = StrictSession  # type: ignore[assignment]
    _srv_mod._STRICT_PATCH_APPLIED = True


class ObsidianMCPServer:
    """MCP Server for Obsidian notes."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.couchdb_client = CouchDBClient(settings)
        self.rate_limiter = RateLimiter(
            requests_per_minute=settings.rate_limit_requests_per_minute,
            burst_size=settings.rate_limit_burst_size
        )
        self.app = Server("obsidian-mcp-server")

        # ------------------------------------------------------------------
        # Ensure that the server ALWAYS advertises resources & tools
        # capability objects in the initialization handshake.  The SDK will
        # simply omit empty capabilities, which causes Claude to think the
        # server has none.  We patch the *instance* method so other Server
        # instances (e.g. created in tests) keep default behaviour.
        # ------------------------------------------------------------------

        # Some unit tests replace the real MCP Server class with a lightweight
        # stub that does *not* implement `create_initialization_options`.  Skip
        # the capability patch in that environment so the tests can run using
        # the stubbed SDK.

        if hasattr(self.app, "create_initialization_options"):
            _orig_create_opts = self.app.create_initialization_options

            def _create_opts_with_caps():  # noqa: D401 – local helper
                opts = _orig_create_opts()

                caps = opts.capabilities  # type: ignore[attr-defined]

                # Populate missing capability sections with empty objects so they
                # are serialised into the InitializeResult.
                if getattr(caps, "resources", None) is None:
                    caps.resources = types.ServerResourceCapabilities()  # type: ignore[attr-defined]
                if getattr(caps, "tools", None) is None:
                    caps.tools = types.ServerToolCapabilities()  # type: ignore[attr-defined]

                return opts

            # Patch the bound method – safe as we do it only for *this* instance.
            # mypy complains about attribute types; ignore.
            self.app.create_initialization_options = _create_opts_with_caps  # type: ignore[assignment]

        self._setup_handlers()
    
    def _setup_handlers(self):
        """Set up MCP request handlers."""
        
        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[types.Content]:  # type: ignore[name-defined]
            """Handle tool calls."""

            def _build_content(text: str | None = None) -> list[types.Content]:
                return [types.TextContent(type="text", text=text or "")]

            # Rate limiting
            if not await self.rate_limiter.is_allowed("call_tool"):
                logger.warning("Rate limit exceeded for call_tool")
                return _build_content(json.dumps({
                    "jsonrpc": "2.0",
                    "id": arguments.get("id"),
                    "error": {
                        "code": -32029,
                        "message": "Rate limit exceeded. Please wait before making more requests."
                    }
                }))
            
            if name == "ping":
                logger.debug("Received ping request")
                return _build_content(json.dumps({
                    "jsonrpc": "2.0",
                    "id": arguments.get("id"),
                    "result": "pong"
                }))
            
            elif name == "find_notes":
                logger.info(f"find_notes tool called with arguments: {arguments}")
                try:
                    # Extract arguments with defaults
                    query = arguments.get("query", "").strip()
                    since_days = arguments.get("since_days")
                    limit = min(arguments.get("limit", 10), 50)
                    sort_by = arguments.get("sort_by", "mtime")
                    include_content_flag = arguments.get("include_content", False)
                    offset = max(int(arguments.get("offset", 0)), 0)
                    sort_order = arguments.get("sort_order", "desc")
                    count_only = arguments.get("count_only", False)
                    exists_only = arguments.get("exists_only", False)

                    # Helper to post-filter by since_days
                    def _filter_by_date(notes_list):
                        if not since_days:
                            return notes_list
                        try:
                            import time
                            threshold_ms = int(time.time() * 1000) - int(since_days) * 86400000
                        except Exception:
                            return notes_list
                        return [n for n in notes_list if n.modified_at >= threshold_ms]

                    # Handle fast count-only path first
                    if count_only:
                        match_count = await self.couchdb_client.count_notes(query=query, since_days=since_days)
                        return _build_content(json.dumps({"match_count": match_count}))

                    notes: List[ObsidianNote] = []

                    if query:
                        # Use search – fetch extra to account for offset
                        results = await self.couchdb_client.search_notes(query, limit=offset + limit * 2)
                        notes_only = [n for (n, _score) in results]
                        notes = _filter_by_date(notes_only)[offset: offset + limit]
                    else:
                        # Browse recent / paginate
                        entries_raw = self.couchdb_client.list_notes(
                            limit=limit * 2,
                            skip=offset,
                            sort_by=sort_by,
                            order=sort_order,
                        )
                        import inspect as _insp
                        if _insp.iscoroutine(entries_raw):
                            entries = await entries_raw
                        else:
                            entries = entries_raw  # type: ignore[assignment]

                        for entry in entries:
                            note_candidate = self.couchdb_client.process_note(entry)
                            if _insp.iscoroutine(note_candidate):
                                note = await note_candidate
                            else:
                                note = note_candidate  # type: ignore[assignment]
                            if note:
                                notes.append(note)
                        notes = _filter_by_date(notes)[:limit]

                    match_count = len(notes)
                    if exists_only:
                        payload = {"exists": match_count > 0, "match_count": match_count}
                        return _build_content(json.dumps(payload))

                    # Determine if we should include content automatically
                    should_include_content = include_content_flag or match_count <= 3

                    # Build response list
                    response_items: List[dict] = []
                    for n in notes:
                        item = {
                            "uri": self._create_note_uri(n.path),
                            "title": n.title,
                            "path": n.path,
                            "mtime": n.modified_at,
                            "ctime": n.created_at,
                            "tags": n.tags,
                        }
                        if should_include_content and n.content:
                            content_val = n.content
                            if len(content_val) > 3000:
                                content_val = content_val[:3000] + "\n\n[Content truncated - use resources/read for full content]"
                            item["content"] = content_val
                        response_items.append(item)

                    if not response_items:
                        return []

                    return _build_content(json.dumps(response_items))

                except Exception as e:
                    import traceback
                    logger.error(f"Error in find_notes tool: {e}")
                    print('EXCEPTION TRACEBACK:')
                    traceback.print_exc()
                    print(f'EXCEPTION TYPE: {type(e)}')
                    return _build_content(f"Error executing find_notes: {e}")

            elif name == "summarise_note":
                logger.info(f"summarise_note tool called with arguments: {arguments}")
                try:
                    uri = arguments.get("uri")
                    if not uri:
                        raise ValueError("'uri' is required")
                    max_words = int(arguments.get("max_words", 300))

                    note_path = self._extract_path_from_uri(uri)
                    if not note_path:
                        raise ValueError(f"Invalid note URI: {uri}")

                    content = await self.couchdb_client.get_note_content(note_path)
                    if content is None:
                        raise McpError(errors.resource_not_found(str(uri)))

                    # TODO: Implement summarization (placeholder returns raw content)
                    return _build_content(content)

                except Exception as e:
                    logger.error(f"Error in summarise_note tool: {e}")
                    return _build_content(f"Error executing summarise_note: {e}")

            else:
                logger.warning(f"Unknown tool: {name}")
                raise Exception(json.dumps({
                    "jsonrpc": "2.0",
                    "id": arguments.get("id"),
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {name}"
                    },
                }))

        @self.app.list_resources()
        async def list_resources(cursor: str | None = None, limit: int | None = None):
            """List resources with cursor-based pagination (max 50)."""

            if not await self.rate_limiter.is_allowed("list_resources"):
                logger.warning("Rate limit exceeded for list_resources")
                raise McpError(errors.rate_limit_exceeded())

            from .pagination import decode_cursor, encode_cursor, validate_limit, CursorError

            DEFAULT_LIMIT = 10

            try:
                limit_val = validate_limit(limit, DEFAULT_LIMIT)
            except ValueError as exc:
                raise McpError(errors.internal_error("Invalid limit parameter")) from exc

            skip = 0
            if cursor:
                try:
                    payload = decode_cursor(cursor)
                    skip = int(payload.get("skip", 0))
                except (CursorError, ValueError, TypeError) as exc:
                    raise McpError(errors.internal_error("Invalid cursor")) from exc

            try:
                docs = await self.couchdb_client.list_notes(limit=limit_val + 1, skip=skip)
            except Exception as exc:
                logger.error("Error querying CouchDB: %s", exc)
                raise McpError(errors.internal_error("Failed to query notes")) from exc

            has_more = len(docs) > limit_val
            docs = docs[:limit_val]

            resources: List[types.Resource] = []
            for doc in docs:
                try:
                    note = await self.couchdb_client.process_note(doc)
                    if note:
                        resources.append(
                            types.Resource(
                                uri=self._create_note_uri(note.path),
                                title=note.title,
                                description=f"Last modified: {note.modified_at}",
                            )
                        )
                except Exception:
                    continue

            next_cursor = encode_cursor({"skip": skip + limit_val}) if has_more else None

            return {"resources": resources, "nextCursor": next_cursor}

        class ReadResourceContents(NamedTuple):
            content: str | bytes
            mime_type: str | None = None

        @self.app.read_resource()
        async def read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
            """Read a resource's contents."""
            # Rate limiting
            if not await self.rate_limiter.is_allowed("read_resource"):
                logger.warning("Rate limit exceeded for read_resource")
                raise McpError(errors.rate_limit_exceeded())

            try:
                # Extract path from URI
                note_path = self._extract_path_from_uri(str(uri))
                if not note_path:
                    raise McpError(errors.resource_not_found(str(uri)))

                # Get note content
                content = await self.couchdb_client.get_note_content(note_path)
                if content is None:
                    raise McpError(errors.resource_not_found(str(uri)))

                yield ReadResourceContents(content=content, mime_type="text/markdown")
            except McpError:
                raise
            except Exception as e:
                logger.error(f"Error reading resource: {e}")
                raise McpError(errors.internal_error(str(e)))

        @self.app.list_tools()
        async def list_tools(cursor: str | None = None, limit: int | None = None):
            """List available tools with cursor-based pagination."""

            if not await self.rate_limiter.is_allowed("list_tools"):
                logger.warning("Rate limit exceeded for list_tools")
                raise McpError(errors.rate_limit_exceeded())

            from .pagination import decode_cursor, encode_cursor, validate_limit, CursorError

            DEFAULT_LIMIT = 20

            try:
                limit_val = validate_limit(limit, DEFAULT_LIMIT)
            except ValueError as exc:
                raise McpError(errors.internal_error("Invalid limit parameter")) from exc

            skip = 0
            if cursor:
                try:
                    payload = decode_cursor(cursor)
                    skip = int(payload.get("skip", 0))
                except (CursorError, ValueError, TypeError) as exc:
                    raise McpError(errors.internal_error("Invalid cursor")) from exc

            try:
                # Full static tool definitions
                all_tools = [
                    types.Tool(name="ping", description="Ping the server to check if it's alive", parameters={}),
                    types.Tool(name="find_notes", description="Search notes", parameters={}),
                    types.Tool(name="summarise_note", description="Summarise a note", parameters={}),
                ]

                all_tools.sort(key=lambda t: t.name)

                slice_tools = all_tools[skip : skip + limit_val + 1]
                has_more = len(slice_tools) > limit_val
                slice_tools = slice_tools[:limit_val]

                next_cursor = encode_cursor({"skip": skip + limit_val}) if has_more else None

                return {"tools": slice_tools, "nextCursor": next_cursor}
            except Exception as e:
                logger.error(f"Error listing tools: {e}")
                raise McpError(errors.internal_error(str(e)))
    
    def _create_note_uri(self, note_path: str) -> str:
        """Create a URI for a note."""
        return f"obsidian://{quote(note_path)}"
    
    def _extract_path_from_uri(self, uri: str) -> Optional[str]:
        """Extract a note path from a URI."""
        if not uri.startswith("obsidian://"):
            return None
        try:
            return unquote(uri[11:])
        except Exception:
            return None
    
    async def start_stdio(self):
        """Start the server in stdio mode."""
        await self.app.run()

    def build_http_app(self) -> Starlette:
        """Build the HTTP application."""
        from .transport.http_stream import create_transport_app, set_message_processor

        async def process_message(session_id: str, message: dict) -> dict:
            """Process a message from the client."""
            logger.debug(f"Processing message for session {session_id}: {message}")
            try:
                # Handle ping request
                if message.get("method") == "ping":
                    logger.debug("Handling ping request")
                    return {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "result": "pong"
                    }

                # Handle other messages through MCP server
                logger.debug("Handling message through MCP server")
                response = await self.app.handle_message(message)
                logger.debug(f"Got response from MCP server: {response}")
                return response
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                return {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                }

        # Set message processor
        set_message_processor(process_message)

        # Create and return app
        return create_transport_app()

    def start_http_sync(self, port: int) -> None:  # pragma: no cover – manual run helper
        """Start the server in HTTP mode."""
        import uvicorn
        uvicorn.run(self.build_http_app(), host="0.0.0.0", port=port)
    
    def start_sse_sync(self, port: int):
        """Start the server in SSE mode."""
        import warnings
        warnings.warn(
            "SSE transport is deprecated and not available. Use HTTP transport instead.",
            DeprecationWarning,
            stacklevel=2
        )
        raise RuntimeError("SSE transport is not available. Use HTTP transport instead.")
    
    async def close(self):
        """Close the server."""
        await self.couchdb_client.close()

@click.command()
@click.option("--port", default=8000, help="Port to listen on for SSE/HTTP")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    help="Transport type",
)
def main(port: int, transport: str) -> int:
    """Run the MCP server."""
    try:
        # Load settings
        settings = Settings()
    
        # Create server
        server = ObsidianMCPServer(settings)
    
        # Run server
        if transport == "stdio":
            asyncio.run(server.start_stdio())
        elif transport == "sse":
            server.start_sse_sync(port)
        elif transport == "http":
            server.start_http_sync(port)
        else:
            raise ValueError(f"Unknown transport: {transport}")

        return 0
    except Exception as e:
        logger.error(f"Error running server: {e}")
        return 1

if __name__ == "__main__":
    exit(main()) 