"""Streamable HTTP transport for MCP (Task 4).

Implements the spec's single-endpoint model:
    • POST /messages/          – client → server JSON-RPC message
    • GET  /messages/          – server → client event-stream (text/event-stream)

Clients identify their logical session with the ``Mcp-Session-Id`` header.
On first contact the server generates a UUID and returns it in the response
header so the client can re-use it.

This module intentionally **does not** start the MCP low-level ServerSession;
it only provides a Starlette router that exposes a pair of AnyIO streams
(read/write) per session.  The embedding application (``ObsidianMCPServer``)
can then feed those streams into ``self.app.run``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from typing import AsyncIterator, Dict, Tuple, Callable, List, Optional, Any, Set

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse, JSONResponse
from starlette.routing import Route
from obsidian_mcp_server.rate_limiter import RateLimiter
import asyncio
from starlette.background import BackgroundTask
import os
try:
    from mcp.server.models import InitializationOptions
except ImportError:
    from pydantic import BaseModel
    from mcp.types import ServerCapabilities
    class InitializationOptions(BaseModel):
        server_name: str
        server_version: str
        capabilities: ServerCapabilities
        instructions: str | None = None
from mcp.server.lowlevel.server import Server
from obsidian_mcp_server.config import Settings
from obsidian_mcp_server.server import ObsidianMCPServer

__all__ = ["create_transport_app", "SessionStreams"]

SessionStreams = Tuple[
    MemoryObjectSendStream[str],   # read_stream proxy: client→server (Send into server read side)
    MemoryObjectSendStream[str],   # write_stream: server→client
]

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Flag to detect test mode
IS_TEST_MODE = os.environ.get("PYTEST_CURRENT_TEST") is not None

# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

class SessionManager:
    """Manages client sessions and their message queues."""

    def __init__(self):
        self._sessions: Dict[str, Any] = {}
        self._running: Set[str] = set()
        self._message_processor: Optional[Callable[[str, dict], Awaitable[dict]]] = None
        self._task_group: Optional[anyio.TaskGroup] = None
        self._ready_events: Dict[str, asyncio.Event] = {}
        self._processor_tasks: Dict[str, asyncio.Task] = {}

    def create_session(self) -> str:
        """Create a new session and return its ID."""
        logger.debug("Creating new session")
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = {
            "client_queue": asyncio.Queue(),
            "server_queue": asyncio.Queue(),
            "running": False
        }
        self._ready_events[session_id] = asyncio.Event()
        logger.debug(f"Created session {session_id}")
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a session by ID."""
        logger.debug(f"Getting session {session_id}")
        return self._sessions.get(session_id)

    def is_running(self, session_id: str) -> bool:
        """Check if a session is running."""
        logger.debug(f"Checking if session {session_id} is running")
        return session_id in self._running

    def mark_running(self, session_id: str) -> None:
        """Mark a session as running."""
        logger.debug(f"Marking session {session_id} as running")
        self._running.add(session_id)
        self._ready_events[session_id].set()

    def get_server_streams(self, session_id: str):
        """Get the server's read/write streams for a session."""
        logger.debug(f"Getting server streams for session {session_id}")
        session = self._sessions[session_id]
        return session["server_queue"], session["client_queue"]

    def set_message_processor(self, processor: Callable[[str, dict], Awaitable[dict]]) -> None:
        """Set the message processor callback."""
        logger.debug("Setting message processor")
        self._message_processor = processor

    async def start_message_processor(self, session_id: str) -> None:
        """Start the message processor for a session."""
        logger.debug(f"Starting message processor for session {session_id}")
        session = self.get_session(session_id)
        if not session:
            logger.error(f"Session {session_id} not found")
            return

        # Create message processor task
        async def _run_processor():
            logger.debug(f"Message processor started for session {session_id}")
            try:
                while True:
                    # Get message from server queue with timeout
                    logger.debug(f"Waiting for message from server queue for session {session_id}")
                    try:
                        message = await asyncio.wait_for(session["server_queue"].get(), timeout=2.0)
                        logger.debug(f"Got message from server queue: {message}")

                        # Process message
                        logger.debug(f"Processing message for session {session_id}")
                        if not self._message_processor:
                            logger.error("No message processor set")
                            continue

                        response = await self._message_processor(session_id, message)
                        logger.debug(f"Got response from message processor: {response}")

                        # Put response in client queue
                        logger.debug(f"Putting response in client queue for session {session_id}")
                        await session["client_queue"].put(response)
                        logger.debug(f"Response put in client queue for session {session_id}")

                        # If this was a ping request, we're done
                        if message.get("method") == "ping":
                            logger.debug("Ping request processed, closing message processor")
                            break
                    except asyncio.TimeoutError:
                        logger.debug(f"Timeout waiting for message from server queue for session {session_id}")
                        continue
            except Exception as e:
                logger.error(f"Error in message processor for session {session_id}: {e}")
                # Put error in client queue
                error_message = {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                }
                await session["client_queue"].put(error_message)
            finally:
                logger.debug(f"Message processor finished for session {session_id}")

        # Start message processor task
        task = asyncio.create_task(_run_processor())
        session["message_processor_task"] = task
        logger.debug(f"Message processor task created for session {session_id}")

    async def process_messages(self, session_id: str) -> None:
        """Process messages from the server queue and put responses in the client queue."""
        logger.debug(f"Starting message processor for session {session_id}")
        session = self._sessions[session_id]
        while True:
            try:
                # Get message from server queue with timeout
                logger.debug(f"Waiting for message from server queue for session {session_id}")
                try:
                    message = await asyncio.wait_for(session["server_queue"].get(), timeout=2.0)
                    logger.debug(f"Got message from server queue for session {session_id}: {message}")
                except asyncio.TimeoutError:
                    logger.debug(f"Timeout waiting for message from server queue for session {session_id}")
                    continue

                # Process message
                if self._message_processor:
                    logger.debug(f"Processing message for session {session_id}")
                    try:
                        response = await asyncio.wait_for(
                            self._message_processor(session_id, message),
                            timeout=2.0
                        )
                        logger.debug(f"Got response from message processor for session {session_id}: {response}")

                        # Put response in client queue
                        logger.debug(f"Putting response in client queue for session {session_id}")
                        await session["client_queue"].put(response)
                        logger.debug(f"Response put in client queue for session {session_id}")

                        # If this was a ping request, we're done
                        if message.get("method") == "ping":
                            logger.debug(f"Ping request processed for session {session_id}, closing processor")
                            return
                    except asyncio.TimeoutError:
                        logger.error(f"Timeout processing message for session {session_id}")
                        response = {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "error": {
                                "code": -32603,
                                "message": "Timeout processing message"
                            }
                        }
                        await session["client_queue"].put(response)
                else:
                    logger.error(f"No message processor set for session {session_id}")
                    await session["client_queue"].put({
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {
                            "code": -32603,
                            "message": "Internal error: No message processor set"
                        }
                    })
            except Exception as e:
                logger.error(f"Error processing message for session {session_id}: {e}")
                await session["client_queue"].put({
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                })

    async def cleanup_session(self, session_id: str) -> None:
        """Clean up a session and its resources."""
        logger.debug(f"Cleaning up session {session_id}")
        if session_id in self._processor_tasks:
            task = self._processor_tasks.pop(session_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if session_id in self._running:
            self._running.remove(session_id)
        if session_id in self._sessions:
            del self._sessions[session_id]
        if session_id in self._ready_events:
            del self._ready_events[session_id]

# Global session manager
_session_mgr = SessionManager()

# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

# Session state for each HTTP client
class HttpSession:
    def __init__(self, server: Server, initialization_options: InitializationOptions):
        self.input_stream = anyio.create_memory_object_stream(100)
        self.output_stream = anyio.create_memory_object_stream(100)
        self.server_task = None
        self.server = server
        self.initialization_options = initialization_options
        self.closed = False

    async def start(self):
        # Start the MCP server for this session
        self.server_task = asyncio.create_task(
            self.server.run(
                self.input_stream[0],
                self.output_stream[1],
                self.initialization_options,
            )
        )

    async def close(self):
        self.closed = True
        if self.server_task:
            self.server_task.cancel()
            try:
                await self.server_task
            except Exception:
                pass
        await self.input_stream[0].aclose()
        await self.output_stream[1].aclose()

# Global session registry
_sessions = {}

# Helper to create InitializationOptions with patched capabilities
def build_initialization_options(server: ObsidianMCPServer):
    # Use the patched create_initialization_options
    return server.app.create_initialization_options()

# HTTP POST handler
async def _handle_post(request: Request) -> Response:
    try:
        message = await request.json()
        # Get or create session
        session_id = request.headers.get("Mcp-Session-Id")
        if not session_id or session_id not in _sessions:
            # New session
            session_id = str(uuid.uuid4())
            # Build the ObsidianMCPServer and get the MCP Server instance
            settings = Settings()
            obsidian_server = ObsidianMCPServer(settings)
            mcp_server = obsidian_server.app
            init_opts = build_initialization_options(obsidian_server)
            session = HttpSession(mcp_server, init_opts)
            await session.start()
            _sessions[session_id] = session
        else:
            session = _sessions[session_id]
        # Write message to input stream
        await session.input_stream[1].send(message)
        # Read response from output stream
        response = await session.output_stream[0].receive()
        return JSONResponse(response, headers={"Mcp-Session-Id": session_id})
    except Exception as e:
        logger.error(f"Error handling POST request: {e}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }, status_code=500)

# HTTP GET handler for SSE
async def _handle_get(request: Request) -> Response:
    session_id = request.headers.get("Mcp-Session-Id")
    if not session_id or session_id not in _sessions:
        return JSONResponse({"error": "Mcp-Session-Id header required or session not found"}, status_code=400)
    session = _sessions[session_id]
    async def event_stream():
        try:
            while not session.closed:
                try:
                    message = await asyncio.wait_for(session.output_stream[0].receive(), timeout=2.0)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        except Exception as e:
            logger.error(f"Error in SSE event stream: {e}")
            yield f"data: {json.dumps({'jsonrpc': '2.0', 'error': {'code': -32603, 'message': str(e)}})}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_transport_app() -> Starlette:
    """Create the Starlette application for the streamable HTTP transport."""
    logger.debug("Creating transport app")
    return Starlette(routes=[
        Route("/messages/", _handle_post, methods=["POST"]),
        Route("/messages/", _handle_get, methods=["GET"]),
    ])

def set_message_processor(processor: Callable[[str, dict], Awaitable[dict]]) -> None:
    """Set the message processor callback."""
    _session_mgr.set_message_processor(processor) 