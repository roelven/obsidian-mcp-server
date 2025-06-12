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

async def _handle_post(request: Request) -> Response:
    """Handle POST requests to /messages/."""
    logger.debug("Handling POST request")
    try:
        # Get message from request
        message = await request.json()
        logger.debug(f"Got message: {message}")

        # Create session
        session_id = _session_mgr.create_session()
        logger.debug(f"Created session: {session_id}")

        # Put message in server queue
        logger.debug("Putting message in server queue")
        await _session_mgr.get_session(session_id)["server_queue"].put(message)

        # Start message processor
        logger.debug("Starting message processor")
        await _session_mgr.start_message_processor(session_id)

        # Return session ID
        logger.debug("Returning session ID")
        return Response(
            status_code=202,
            headers={"Mcp-Session-Id": session_id}
        )
    except Exception as e:
        logger.error(f"Error handling POST request: {e}")
        return Response(
            status_code=500,
            content=json.dumps({
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }),
            media_type="application/json"
        )

async def _handle_get(request: Request) -> Response:
    """Handle GET requests to /messages/."""
    logger.debug("Received GET request")
    try:
        # Get session ID
        session_id = request.headers.get("Mcp-Session-Id")
        if not session_id:
            logger.error("No session ID in headers")
            return JSONResponse(
                {"error": "Mcp-Session-Id header required"},
                status_code=400
            )

        # Get session
        session = _session_mgr.get_session(session_id)
        if not session:
            logger.error(f"Session {session_id} not found")
            return JSONResponse(
                {"error": "Session not found"},
                status_code=404
            )

        # Return SSE stream
        logger.debug(f"Starting SSE stream for session {session_id}")
        return StreamingResponse(
            _event_generator(session),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            }
        )
    except Exception as e:
        logger.error(f"Error handling GET request: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500
        )

async def _event_generator(session: Dict[str, Any]):
    """Generate SSE events from the client queue."""
    logger.debug("Starting event generator")
    try:
        while True:
            # Get message from client queue with timeout
            logger.debug("Waiting for message from client queue")
            try:
                message = await asyncio.wait_for(session["client_queue"].get(), timeout=2.0)
                logger.debug(f"Got message from client queue: {message}")

                # Send as SSE event
                logger.debug("Sending SSE event")
                yield f"data: {json.dumps(message)}\n\n".encode("utf-8")
                logger.debug("SSE event sent")

                # If this was a ping response, we're done
                if message.get("jsonrpc") == "2.0" and message.get("result") == "pong":
                    logger.debug("Received ping response, closing event generator")
                    break
            except asyncio.TimeoutError:
                logger.debug("Timeout waiting for message from client queue")
                # Send a keep-alive comment
                yield b": keep-alive\n\n"
                continue
    except Exception as e:
        logger.error(f"Error in event generator: {e}")
        # Send error as SSE event
        error_message = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }
        yield f"data: {json.dumps(error_message)}\n\n".encode("utf-8")
        raise
    finally:
        logger.debug("Event generator finished")

# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_transport_app() -> Starlette:
    """Create the Starlette application for the streamable HTTP transport."""
    logger.debug("Creating transport app")
    return Starlette(routes=[
        Route("/messages/", _handle_post, methods=["POST"]),
        Route("/messages/", _handle_get, methods=["GET"])
    ])

def set_message_processor(processor: Callable[[str, dict], Awaitable[dict]]) -> None:
    """Set the message processor callback."""
    _session_mgr.set_message_processor(processor) 