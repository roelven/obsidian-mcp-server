"""Main MCP server implementation for Obsidian notes."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

import anyio
import click
import mcp.types as types
from mcp.server.lowlevel import Server
from pydantic import AnyUrl

from .config import Settings
from .couchdb_client import CouchDBClient
from .types import ObsidianNote

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ObsidianMCPServer:
    """MCP Server for Obsidian notes."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.couchdb_client = CouchDBClient(settings)
        self.app = Server("obsidian-mcp-server")
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Set up MCP request handlers."""
        
        @self.app.list_resources()
        async def list_resources() -> List[types.Resource]:
            """List available Obsidian notes as MCP resources."""
            try:
                # Limit to a reasonable number to prevent overwhelming AI clients
                # AI clients should ask for specific notes rather than processing everything
                entries = await self.couchdb_client.list_notes(limit=50)
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
                
                logger.info(f"Listed {len(resources)} resources (limited to 50 for performance)")
                return resources
            except Exception as e:
                logger.error(f"Error listing resources: {e}")
                return []
        
        @self.app.read_resource()
        async def read_resource(uri: AnyUrl) -> str:
            """Read the content of a specific Obsidian note."""
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
                return content
            except Exception as e:
                logger.error(f"Error reading resource {uri}: {e}")
                raise ValueError(f"Failed to read resource: {e}")
        
        @self.app.list_tools()
        async def list_tools() -> List[types.Tool]:
            """List available tools."""
            return [
                types.Tool(
                    name="search_notes",
                    description="Search through Obsidian notes by title, content, or tags. Use this instead of listing all notes.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query to find relevant notes"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return (default: 10, max: 50)",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50
                            }
                        },
                        "required": ["query"]
                    }
                )
            ]
        
        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict) -> List[types.TextContent]:
            """Handle tool calls."""
            if name == "search_notes":
                try:
                    query = arguments.get("query", "")
                    limit = min(arguments.get("limit", 10), 50)  # Cap at 50
                    
                    if not query:
                        return [types.TextContent(
                            type="text",
                            text="Error: Search query is required"
                        )]
                    
                    # Search notes
                    results = await self.couchdb_client.search_notes(query, limit)
                    
                    if not results:
                        return [types.TextContent(
                            type="text",
                            text=f"No notes found matching '{query}'"
                        )]
                    
                    # Format results
                    response_lines = [f"Found {len(results)} notes matching '{query}':\n"]
                    
                    for note, score in results:
                        response_lines.append(f"**{note.title}**")
                        response_lines.append(f"Path: {note.path}")
                        response_lines.append(f"Score: {score:.1f}")
                        if note.tags:
                            response_lines.append(f"Tags: {', '.join(note.tags)}")
                        response_lines.append(f"URI: {self._create_note_uri(note.path)}")
                        response_lines.append("")  # Empty line
                    
                    return [types.TextContent(
                        type="text",
                        text="\n".join(response_lines)
                    )]
                    
                except Exception as e:
                    logger.error(f"Error in search_notes tool: {e}")
                    return [types.TextContent(
                        type="text",
                        text=f"Error searching notes: {e}"
                    )]
            else:
                return [types.TextContent(
                    type="text",
                    text=f"Unknown tool: {name}"
                )]
    
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