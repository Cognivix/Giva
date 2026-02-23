"""MCP connection lifecycle: connect, call tools, reconnect, shutdown.

All async MCP operations run on a single dedicated event loop thread.
This is necessary because:
- ``MCPConnection`` uses ``asyncio.Lock`` for concurrency safety.
- Stdio connections are persistent (subprocess stays alive between calls).
- ``execute()`` calls come from different thread-pool threads; each would
  spin up its own event loop with ``asyncio.run()``, breaking persistent
  connections.

The module-level helpers ``run_mcp_coro()`` and ``shutdown_mcp_loop()``
provide the sync↔async bridge used by the rest of the MCP agent package.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import AsyncExitStack
from typing import Any, Optional

from giva.agents.mcp_agent.config import MCPServerConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dedicated MCP event loop (background daemon thread)
# ---------------------------------------------------------------------------

_mcp_loop: Optional[asyncio.AbstractEventLoop] = None
_mcp_thread: Optional[threading.Thread] = None
_mcp_lock = threading.Lock()  # protects loop/thread creation


def _get_mcp_loop() -> asyncio.AbstractEventLoop:
    """Return the dedicated MCP event loop, creating it on first call."""
    global _mcp_loop, _mcp_thread
    with _mcp_lock:
        if _mcp_loop is None or not _mcp_loop.is_running():
            _mcp_loop = asyncio.new_event_loop()
            _mcp_thread = threading.Thread(
                target=_mcp_loop.run_forever,
                daemon=True,
                name="mcp-event-loop",
            )
            _mcp_thread.start()
            log.debug("MCP event loop started on thread %s", _mcp_thread.name)
    return _mcp_loop


def run_mcp_coro(coro, timeout: float = 60) -> Any:
    """Submit an async coroutine to the MCP event loop and block for the result.

    This is the primary sync→async bridge.  Safe to call from any thread
    (including the default thread-pool executor used by FastAPI).
    """
    loop = _get_mcp_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def shutdown_mcp_loop() -> None:
    """Stop the MCP event loop and wait for the thread to finish.

    Called once during server shutdown to clean up background resources.
    """
    global _mcp_loop, _mcp_thread
    with _mcp_lock:
        if _mcp_loop is not None and _mcp_loop.is_running():
            _mcp_loop.call_soon_threadsafe(_mcp_loop.stop)
            log.debug("MCP event loop stop requested")
        if _mcp_thread is not None and _mcp_thread.is_alive():
            _mcp_thread.join(timeout=5)
            log.debug("MCP event loop thread joined")
        _mcp_loop = None
        _mcp_thread = None


# ---------------------------------------------------------------------------
# MCPConnection — one per configured MCP server
# ---------------------------------------------------------------------------


class MCPConnection:
    """Manages the lifecycle of a connection to one MCP server.

    For **stdio**: starts the subprocess on ``connect()``, keeps it alive
    between calls, reconnects automatically on failure.

    For **http**: each ``connect()`` validates reachability; sessions are
    lightweight and created fresh.

    All public methods are async and must be called from the MCP event loop
    (via ``run_mcp_coro``).
    """

    def __init__(self, server_config: MCPServerConfig):
        self._config = server_config
        self._exit_stack: Optional[AsyncExitStack] = None
        self._session = None  # mcp.ClientSession
        self._tools: list = []  # list of mcp.types.Tool
        self._connected = False
        self._lock = asyncio.Lock()

    @property
    def tools(self) -> list:
        """Cached list of ``mcp.types.Tool`` objects from last discovery."""
        return list(self._tools)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---- connect / disconnect ------------------------------------------------

    async def connect(self) -> bool:
        """Establish connection and discover tools.  Returns True on success."""
        async with self._lock:
            return await self._connect_inner()

    async def _connect_inner(self) -> bool:
        """Internal connect — caller must hold ``self._lock``."""
        from giva.agents.mcp_agent._compat import (
            ClientSession,
            StdioServerParameters,
            stdio_client,
            streamable_http_client,
        )

        # Clean up any previous connection
        await self._cleanup()

        try:
            self._exit_stack = AsyncExitStack()

            if self._config.transport == "stdio":
                env = {**os.environ, **self._config.env} if self._config.env else None
                params = StdioServerParameters(
                    command=self._config.command,
                    args=self._config.args,
                    env=env,
                )
                read_stream, write_stream = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
            elif self._config.transport == "http":
                read_stream, write_stream = await self._exit_stack.enter_async_context(
                    streamable_http_client(self._config.url)
                )
            else:
                log.error("MCP %s: unsupported transport %r",
                          self._config.name, self._config.transport)
                return False

            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await self._session.initialize()

            # Discover tools
            result = await self._session.list_tools()
            self._tools = list(result.tools)
            self._connected = True

            log.info(
                "MCP %s connected (%s): %d tools discovered",
                self._config.name, self._config.transport, len(self._tools),
            )
            return True

        except Exception as exc:
            log.warning("MCP %s connect failed: %s", self._config.name, exc)
            await self._cleanup()
            return False

    async def _cleanup(self) -> None:
        """Release session and transport resources."""
        self._connected = False
        self._session = None
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
            self._exit_stack = None

    # ---- call_tool -----------------------------------------------------------

    async def call_tool(
        self, tool_name: str, arguments: dict
    ) -> tuple[bool, str]:
        """Call a tool on the connected MCP server.

        Returns ``(success, text_output)``.  For stdio transports,
        automatically reconnects once on connection failure.
        """
        async with self._lock:
            if not self._connected:
                if not await self._connect_inner():
                    return False, f"MCP server {self._config.name} is not connected"

            try:
                return await self._call_tool_inner(tool_name, arguments)
            except Exception as exc:
                log.warning("MCP %s tool %s error: %s",
                            self._config.name, tool_name, exc)

                # Reconnect once for stdio (subprocess may have died)
                if self._config.transport == "stdio":
                    log.info("MCP %s: attempting reconnect after failure",
                             self._config.name)
                    if await self._connect_inner():
                        try:
                            return await self._call_tool_inner(tool_name, arguments)
                        except Exception as exc2:
                            log.error("MCP %s reconnect-retry failed: %s",
                                      self._config.name, exc2)
                            return False, str(exc2)

                return False, str(exc)

    async def _call_tool_inner(
        self, tool_name: str, arguments: dict
    ) -> tuple[bool, str]:
        """Execute the tool call (caller holds lock, connection is live)."""
        result = await asyncio.wait_for(
            self._session.call_tool(tool_name, arguments),
            timeout=self._config.timeout_seconds,
        )
        # Extract text from content blocks
        texts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                texts.append(block.text)
            elif hasattr(block, "data"):
                mime = getattr(block, "mimeType", "unknown")
                texts.append(f"[binary content: {mime}]")
            else:
                texts.append(str(block))

        output = "\n".join(texts)
        return (not result.isError, output)

    # ---- lifecycle -----------------------------------------------------------

    async def shutdown(self) -> None:
        """Cleanly shut down the connection and release resources."""
        async with self._lock:
            await self._cleanup()
        log.info("MCP %s shut down", self._config.name)

    async def health_check(self) -> bool:
        """Lightweight connectivity check.  Returns True if healthy."""
        if not self._connected or self._session is None:
            return False
        try:
            await asyncio.wait_for(self._session.list_tools(), timeout=5)
            return True
        except Exception:
            log.debug("MCP %s health check failed", self._config.name)
            self._connected = False
            return False
