"""Tests for MCP connection lifecycle management."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from giva.agents.mcp_agent.config import MCPServerConfig
from giva.agents.mcp_agent.lifecycle import (
    MCPConnection,
    _get_mcp_loop,
    run_mcp_coro,
    shutdown_mcp_loop,
)

# All patches target _compat because lifecycle.py does:
#   from giva.agents.mcp_agent._compat import ClientSession, stdio_client, ...
_COMPAT = "giva.agents.mcp_agent._compat"


@pytest.fixture
def stdio_config():
    return MCPServerConfig(
        name="test_server",
        transport="stdio",
        command="python",
        args=["fake_server.py"],
        timeout_seconds=5,
    )


@pytest.fixture
def http_config():
    return MCPServerConfig(
        name="test_http",
        transport="http",
        url="http://localhost:9999/mcp",
        timeout_seconds=5,
    )


def _make_mock_tool(name="read_file", description="Read a file from disk"):
    """Create a mock MCP Tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    return tool


def _make_mock_text_content(text="result text"):
    """Create a mock TextContent block."""
    block = MagicMock()
    block.text = text
    return block


def _setup_stdio_mocks(mock_stdio, mock_session_cls, tools, call_result=None):
    """Wire up the mock async context managers for stdio transport."""
    mock_read = MagicMock()
    mock_write = MagicMock()
    mock_stdio_ctx = AsyncMock()
    mock_stdio_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
    mock_stdio_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_stdio.return_value = mock_stdio_ctx

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    tools_result = MagicMock()
    tools_result.tools = tools
    mock_session.list_tools = AsyncMock(return_value=tools_result)

    if call_result is not None:
        mock_session.call_tool = AsyncMock(return_value=call_result)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_session_cls.return_value = mock_session_ctx

    return mock_session


class TestMCPEventLoop:
    def test_get_mcp_loop_creates_loop(self):
        """The MCP loop should be created and running."""
        try:
            loop = _get_mcp_loop()
            assert loop is not None
            assert loop.is_running()
        finally:
            shutdown_mcp_loop()

    def test_run_mcp_coro_bridges_sync_async(self):
        """run_mcp_coro should execute async code from a sync context."""
        try:
            async def _add(a, b):
                return a + b

            result = run_mcp_coro(_add(3, 4), timeout=5)
            assert result == 7
        finally:
            shutdown_mcp_loop()

    def test_shutdown_mcp_loop_cleans_up(self):
        """After shutdown, the loop should no longer be running."""
        loop = _get_mcp_loop()
        assert loop.is_running()
        shutdown_mcp_loop()
        assert not loop.is_running()


class TestMCPConnection:
    @patch(f"{_COMPAT}.stdio_client")
    @patch(f"{_COMPAT}.ClientSession")
    @patch(f"{_COMPAT}.StdioServerParameters")
    def test_connect_stdio_success(
        self, mock_params_cls, mock_session_cls, mock_stdio, stdio_config
    ):
        """Stdio connect should succeed and discover tools."""
        tools = [_make_mock_tool("read_file"), _make_mock_tool("write_file")]
        _setup_stdio_mocks(mock_stdio, mock_session_cls, tools)
        mock_params_cls.return_value = MagicMock()

        try:
            conn = MCPConnection(stdio_config)
            ok = run_mcp_coro(conn.connect(), timeout=10)
            assert ok is True
            assert conn.is_connected
            assert len(conn.tools) == 2
            assert conn.tools[0].name == "read_file"
        finally:
            run_mcp_coro(conn.shutdown(), timeout=5)
            shutdown_mcp_loop()

    @patch(f"{_COMPAT}.streamable_http_client")
    @patch(f"{_COMPAT}.ClientSession")
    def test_connect_http_success(self, mock_session_cls, mock_http, http_config):
        """HTTP connect should succeed and discover tools."""
        mock_read = MagicMock()
        mock_write = MagicMock()
        mock_http_ctx = AsyncMock()
        mock_http_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_http_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_http.return_value = mock_http_ctx

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        tools_result = MagicMock()
        tools_result.tools = [_make_mock_tool("query_db")]
        mock_session.list_tools = AsyncMock(return_value=tools_result)

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session_ctx

        try:
            conn = MCPConnection(http_config)
            ok = run_mcp_coro(conn.connect(), timeout=10)
            assert ok is True
            assert len(conn.tools) == 1
        finally:
            run_mcp_coro(conn.shutdown(), timeout=5)
            shutdown_mcp_loop()

    def test_connect_failure_graceful(self, stdio_config):
        """Connect should return False on error (MCP SDK not installed)."""
        try:
            conn = MCPConnection(stdio_config)
            ok = run_mcp_coro(conn.connect(), timeout=10)
            assert ok is False
            assert not conn.is_connected
            assert conn.tools == []
        finally:
            shutdown_mcp_loop()

    @patch(f"{_COMPAT}.stdio_client")
    @patch(f"{_COMPAT}.ClientSession")
    @patch(f"{_COMPAT}.StdioServerParameters")
    def test_call_tool_success(
        self, mock_params_cls, mock_session_cls, mock_stdio, stdio_config
    ):
        """call_tool should return text output on success."""
        call_result = MagicMock()
        call_result.isError = False
        call_result.content = [_make_mock_text_content("file contents here")]

        tools = [_make_mock_tool("read_file")]
        _setup_stdio_mocks(mock_stdio, mock_session_cls, tools, call_result)
        mock_params_cls.return_value = MagicMock()

        try:
            conn = MCPConnection(stdio_config)
            run_mcp_coro(conn.connect(), timeout=10)

            success, output = run_mcp_coro(
                conn.call_tool("read_file", {"path": "/tmp/test"}), timeout=10
            )
            assert success is True
            assert "file contents here" in output
        finally:
            run_mcp_coro(conn.shutdown(), timeout=5)
            shutdown_mcp_loop()

    @patch(f"{_COMPAT}.stdio_client")
    @patch(f"{_COMPAT}.ClientSession")
    @patch(f"{_COMPAT}.StdioServerParameters")
    def test_call_tool_error_result(
        self, mock_params_cls, mock_session_cls, mock_stdio, stdio_config
    ):
        """call_tool should return success=False when isError is True."""
        call_result = MagicMock()
        call_result.isError = True
        call_result.content = [_make_mock_text_content("permission denied")]

        tools = [_make_mock_tool("read_file")]
        _setup_stdio_mocks(mock_stdio, mock_session_cls, tools, call_result)
        mock_params_cls.return_value = MagicMock()

        try:
            conn = MCPConnection(stdio_config)
            run_mcp_coro(conn.connect(), timeout=10)

            success, output = run_mcp_coro(
                conn.call_tool("read_file", {"path": "/etc/shadow"}), timeout=10
            )
            assert success is False
            assert "permission denied" in output
        finally:
            run_mcp_coro(conn.shutdown(), timeout=5)
            shutdown_mcp_loop()

    @patch(f"{_COMPAT}.stdio_client")
    @patch(f"{_COMPAT}.ClientSession")
    @patch(f"{_COMPAT}.StdioServerParameters")
    def test_shutdown_cleanup(
        self, mock_params_cls, mock_session_cls, mock_stdio, stdio_config
    ):
        """Shutdown should disconnect and clear state."""
        tools = [_make_mock_tool()]
        _setup_stdio_mocks(mock_stdio, mock_session_cls, tools)
        mock_params_cls.return_value = MagicMock()

        try:
            conn = MCPConnection(stdio_config)
            run_mcp_coro(conn.connect(), timeout=10)
            assert conn.is_connected

            run_mcp_coro(conn.shutdown(), timeout=5)
            assert not conn.is_connected
        finally:
            shutdown_mcp_loop()

    @patch(f"{_COMPAT}.stdio_client")
    @patch(f"{_COMPAT}.ClientSession")
    @patch(f"{_COMPAT}.StdioServerParameters")
    def test_health_check_alive(
        self, mock_params_cls, mock_session_cls, mock_stdio, stdio_config
    ):
        """health_check should return True when connected."""
        tools = [_make_mock_tool()]
        _setup_stdio_mocks(mock_stdio, mock_session_cls, tools)
        mock_params_cls.return_value = MagicMock()

        try:
            conn = MCPConnection(stdio_config)
            run_mcp_coro(conn.connect(), timeout=10)

            healthy = run_mcp_coro(conn.health_check(), timeout=5)
            assert healthy is True
        finally:
            run_mcp_coro(conn.shutdown(), timeout=5)
            shutdown_mcp_loop()

    def test_health_check_disconnected(self, stdio_config):
        """health_check should return False when not connected."""
        try:
            conn = MCPConnection(stdio_config)
            healthy = run_mcp_coro(conn.health_check(), timeout=5)
            assert healthy is False
        finally:
            shutdown_mcp_loop()
