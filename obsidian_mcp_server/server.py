"""Main MCP server implementation for Obsidian notes."""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, NamedTuple, Iterable
from urllib.parse import quote, unquote

import anyio
import click
import mcp.types as types
from mcp.server.lowlevel import Server
from pydantic import AnyUrl

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

    import importlib

    import mcp.types as _types  # noqa: WPS433 – runtime patching is intentional
    from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS  # noqa: WPS433

    # Avoid heavy *pydantic* re-exports until inside the patch body
    from mcp.server import session as _session_mod  # type: ignore

    # Dynamically define subclass to keep a tight coupling with the SDK's
    # current public interface while minimising risk of import cycles.
    class StrictVersionServerSession(_session_mod.ServerSession):  # type: ignore
        """A drop-in replacement that enforces protocol-version matches."""

        async def _received_request(self, responder):  # type: ignore[override]
            match responder.request.root:  # noqa: WPS513 – structural pattern
                case _types.InitializeRequest(params=params):
                    requested_version = params.protocolVersion

                    # Update state: now *initializing*
                    self._initialization_state = _session_mod.InitializationState.Initializing  # type: ignore[attr-defined]
                    self._client_params = params  # type: ignore[assignment]

                    with responder:
                        if requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
                            await responder.respond(
                                _types.ErrorData(
                                    code=-32001,
                                    message="Protocol version mismatch",
                                    data={"supportedVersions": SUPPORTED_PROTOCOL_VERSIONS},
                                )
                            )
                            return

                        await responder.respond(
                            _types.ServerResult(
                                _types.InitializeResult(
                                    protocolVersion=requested_version,
                                    capabilities=_types.ServerCapabilities.model_validate(
                                        self._init_options.capabilities.model_dump(by_alias=True, mode="json", exclude_none=True)  # type: ignore[attr-defined]
                                    ),
                                    serverInfo=_types.Implementation(
                                        name=self._init_options.server_name,  # type: ignore[attr-defined]
                                        version=self._init_options.server_version,  # type: ignore[attr-defined]
                                    ),
                                    instructions=self._init_options.instructions,  # type: ignore[attr-defined]
                                )
                            )
                        )

                        # Mark session as fully initialised
                        self._initialization_state = _session_mod.InitializationState.Initialized  # type: ignore[attr-defined]
                case _:
                    # Delegate all other traffic to the default implementation
                    await super()._received_request(responder)  # type: ignore[misc]

    # Inject our subclass into both the canonical module and the alias cached in
    # *mcp.server.lowlevel.server*
    _session_mod.ServerSession = StrictVersionServerSession  # type: ignore[assignment]
    lowlevel_mod = importlib.import_module("mcp.server.lowlevel.server")
    setattr(lowlevel_mod, "ServerSession", StrictVersionServerSession)


# Apply patch once at import time
_patch_strict_version_validation()


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
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Set up MCP request handlers."""
        
        @self.app.list_resources()
        async def list_resources() -> List[types.Resource]:
            """List available Obsidian notes as MCP resources."""
            # Rate limiting
            if not await self.rate_limiter.is_allowed("list_resources"):
                logger.warning("Rate limit exceeded for list_resources")
                raise ValueError("Rate limit exceeded. Please wait before making more requests.")
            
            try:
                # Limit to a very small number to prevent overwhelming AI clients
                # AI clients should use the find_notes tool for comprehensive access instead
                entries = await self.couchdb_client.list_notes(limit=10, sort_by="mtime")
                resources = []
                
                for entry in entries:
                    # Process the note to get metadata
                    note = await self.couchdb_client.process_note(entry)
                    if not note:
                        continue
                    
                    # Create MCP Resource
                    uri = self._create_note_uri(note.path)
                    resource = types.Resource(
                        uri=AnyUrl(uri),
                        name=note.title,
                        description=f"Path: {note.path}",
                        mimeType="text/markdown"
                    )
                    resources.append(resource)
                
                logger.info(f"Listed {len(resources)} resources (limited to 10 for performance - use tools for more)")
                return resources
            except Exception as e:
                logger.error(f"Error listing resources: {e}")
                return []
        
        # ------------------------------------------------------------------
        # Helper type returned by the handler – lightweight bridge that the
        # upstream SDK converts into `TextResourceContents` / `BlobResourceContents`.
        # ------------------------------------------------------------------

        class ReadResourceContents(NamedTuple):
            content: str | bytes
            mime_type: str | None = None

        @self.app.read_resource()
        async def read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
            """Read the content of a specific Obsidian note.

            Returns an iterable of `ReadResourceContents` so the SDK produces a
            spec-compliant `ReadResourceResult` object.
            """
            # Rate limiting
            if not await self.rate_limiter.is_allowed("read_resource"):
                logger.warning("Rate limit exceeded for read_resource")
                raise ValueError("Rate limit exceeded. Please wait before making more requests.")
            
            try:
                # Extract note path from URI
                note_path = self._extract_path_from_uri(str(uri))
                if not note_path:
                    raise ValueError(f"Invalid resource URI: {uri}")
                
                # Get note content
                content = await self.couchdb_client.get_note_content(note_path)
                if content is None:
                    raise ValueError(f"Note not found: {note_path}")
                
                logger.info(f"Read resource: {note_path}")

                # Wrap into iterable form expected by SDK wrapper
                return [ReadResourceContents(content=content, mime_type="text/markdown")]
            except Exception as e:
                logger.error(f"Error reading resource {uri}: {e}")
                raise ValueError(f"Failed to read resource: {e}")
        
        @self.app.list_tools()
        async def list_tools() -> List[types.Tool]:
            """List available tools."""
            logger.info("Tools requested - returning find_notes and summarise_note")
            return [
                types.Tool(
                    name="find_notes",
                    description=(
                        "Search or browse Obsidian notes with flexible filters. "
                        "If `query` is omitted the tool returns recent notes. "
                        "Automatically includes full note content when <=3 results unless `include_content` is false. "
                        "Set `exists_only` to true to perform a lightweight existence check that returns only a boolean and match count."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search term. Leave empty to fetch recent notes.",
                                "default": ""
                            },
                            "since_days": {
                                "type": "integer",
                                "description": "Only include notes modified in the last N days.",
                                "minimum": 1
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return (default 10, max 50).",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50
                            },
                            "sort_by": {
                                "type": "string",
                                "description": "Sort order for notes.",
                                "enum": ["mtime", "ctime", "path"],
                                "default": "mtime"
                            },
                            "include_content": {
                                "type": "boolean",
                                "description": "Include full note content in each result (auto-enabled for <=3 results).",
                                "default": False
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Number of matching notes to skip (for paging).",
                                "default": 0,
                                "minimum": 0
                            },
                            "sort_order": {
                                "type": "string",
                                "description": "Sort direction (\"desc\" = newest first, \"asc\" = oldest first). Applies to the \"sort_by\" field.",
                                "enum": ["desc", "asc"],
                                "default": "desc"
                            },
                            "count_only": {
                                "type": "boolean",
                                "description": "Return only {match_count:number} without the actual note list.",
                                "default": False
                            },
                            "exists_only": {
                                "type": "boolean",
                                "description": "Return only {exists:boolean, match_count:number} instead of full results.",
                                "default": False
                            }
                        },
                        "required": []
                    }
                ),
                types.Tool(
                    name="summarise_note",
                    description="Generate a short summary (≈ N words) of a single note identified by its MCP URI.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "uri": {
                                "type": "string",
                                "description": "MCP URI of the note to summarise."
                            },
                            "max_words": {
                                "type": "integer",
                                "description": "Maximum words in the summary (default 300).",
                                "default": 300,
                                "minimum": 50,
                                "maximum": 1000
                            }
                        },
                        "required": ["uri"]
                    }
                )
            ]
        
        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict) -> List[types.TextContent]:
            """Handle tool calls."""
            # Rate limiting
            if not await self.rate_limiter.is_allowed("call_tool"):
                logger.warning("Rate limit exceeded for call_tool")
                return [types.TextContent(
                    type="text",
                    text="Rate limit exceeded. Please wait before making more requests."
                )]
            
            if name == "find_notes":
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
                        import json
                        return [types.TextContent(type="text", text=json.dumps({"match_count": match_count}))]

                    notes: List[ObsidianNote] = []

                    if query:
                        # Use search – fetch extra to account for offset
                        results = await self.couchdb_client.search_notes(query, limit=offset + limit * 2)
                        notes_only = [n for (n, _score) in results]
                        notes = _filter_by_date(notes_only)[offset: offset + limit]
                    else:
                        # Browse recent / paginate
                        entries = await self.couchdb_client.list_notes(
                            limit=limit * 2,
                            skip=offset,
                            sort_by=sort_by,
                            order=sort_order,
                        )
                        for entry in entries:
                            note = await self.couchdb_client.process_note(entry)
                            if note:
                                notes.append(note)
                        notes = _filter_by_date(notes)[:limit]

                    match_count = len(notes)
                    if exists_only:
                        payload = {"exists": match_count > 0, "match_count": match_count}
                        import json
                        return [types.TextContent(type="text", text=json.dumps(payload))]

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

                    import json
                    return [types.TextContent(type="text", text=json.dumps(response_items))]

                except Exception as e:
                    logger.error(f"Error in find_notes tool: {e}")
                    return [types.TextContent(type="text", text=f"Error executing find_notes: {e}")]

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
                        raise ValueError(f"Note not found: {note_path}")

                    # Naive summary: first max_words words
                    words = content.split()
                    summary_words = words[:max_words]
                    summary = " ".join(summary_words)
                    if len(words) > max_words:
                        summary += " …"

                    payload = {
                        "uri": uri,
                        "summary": summary,
                        "word_count": len(summary_words)
                    }
                    import json
                    return [types.TextContent(type="text", text=json.dumps(payload))]

                except Exception as e:
                    logger.error(f"Error in summarise_note tool: {e}")
                    return [types.TextContent(type="text", text=f"Error executing summarise_note: {e}")]

            else:
                # Unknown tool – raise so the outer wrapper returns isError=True
                raise ValueError(f"Unknown tool: {name}")
    
    def _create_note_uri(self, note_path: str) -> str:
        """Create MCP URI for a note."""
        vault_id = quote(self.settings.vault_id, safe='')
        encoded_path = quote(note_path, safe='')
        return f"mcp-obsidian://{vault_id}/{encoded_path}"
    
    def _extract_path_from_uri(self, uri: str) -> Optional[str]:
        """Extract note path from MCP URI."""
        try:
            if not uri.startswith("mcp-obsidian://"):
                return None
            
            # Remove scheme
            path_part = uri[15:]  # len("mcp-obsidian://")
            
            # Split vault_id and note_path
            parts = path_part.split('/', 1)
            if len(parts) != 2:
                return None
            
            # Decode the note path
            note_path = unquote(parts[1])
            return note_path
        except Exception:
            return None
    
    async def start_stdio(self):
        """Start the server with stdio transport."""
        from mcp.server.stdio import stdio_server
        
        async with stdio_server() as streams:
            await self.app.run(
                streams[0], streams[1], self.app.create_initialization_options()
            )
    
    def start_sse_sync(self, port: int):
        """Start the server with SSE transport (synchronous version)."""
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Mount, Route
        
        sse = SseServerTransport("/messages/")
        
        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await self.app.run(
                    streams[0], streams[1], self.app.create_initialization_options()
                )
            return Response()
        
        starlette_app = Starlette(
            debug=True,
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages/", app=sse.handle_post_message),
            ],
        )
        
        import uvicorn
        uvicorn.run(starlette_app, host="0.0.0.0", port=port)
    
    async def close(self):
        """Clean up resources."""
        await self.couchdb_client.close()


@click.command()
@click.option("--port", default=8000, help="Port to listen on for SSE")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"]),
    default="stdio",
    help="Transport type",
)
def main(port: int, transport: str) -> int:
    """Main entry point for the Obsidian MCP Server."""
    
    # Load settings
    try:
        settings = Settings()
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return 1
    
    # Create server
    server = ObsidianMCPServer(settings)
    
    async def run_server():
        try:
            # Test CouchDB connection
            if not await server.couchdb_client.test_connection():
                logger.error("Failed to connect to CouchDB")
                return 1
            
            logger.info("Connected to CouchDB successfully")
            
            # Start server
            if transport == "sse":
                logger.info(f"Starting SSE server on port {port}")
                # For SSE, we need to run uvicorn outside the async context
                return 0  # Signal success, actual server start happens below
            else:
                logger.info("Starting stdio server")
                await server.start_stdio()
        except Exception as e:
            logger.error(f"Server error: {e}")
            return 1
        finally:
            if transport != "sse":  # Don't close for SSE as it runs separately
                await server.close()
        
        return 0
    
    # Run the server
    try:
        if transport == "sse":
            # For SSE, test connection first, then start uvicorn
            async def test_connection():
                return await server.couchdb_client.test_connection()
            
            if not anyio.run(test_connection):
                logger.error("Failed to connect to CouchDB")
                return 1
            
            logger.info("Connected to CouchDB successfully")
            logger.info(f"Starting SSE server on port {port}")
            server.start_sse_sync(port)
            return 0
        else:
            result = anyio.run(run_server)
            return result
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        return 0
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    exit(main()) 