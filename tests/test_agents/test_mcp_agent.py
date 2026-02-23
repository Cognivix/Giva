"""Tests for the MCPAgent class."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from giva.agents.mcp_agent.agent import MCPAgent
from giva.agents.mcp_agent.config import MCPServerConfig
from giva.config import GivaConfig


def _make_tool(name, description=None, input_schema=None):
    """Create a mock MCP Tool object."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = input_schema or {
        "type": "object",
        "properties": {},
    }
    return tool


def _make_config(name="test_server"):
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command="python",
        args=["server.py"],
    )


class TestManifestGeneration:
    def test_agent_id_prefixed(self):
        cfg = _make_config("filesystem")
        tools = [_make_tool("read_file", "Read a file from disk")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)
        assert agent.manifest.agent_id == "mcp_filesystem"

    def test_name_formatted(self):
        cfg = _make_config("my_server")
        tools = [_make_tool("tool1")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)
        assert agent.manifest.name == "MCP: My Server"

    def test_description_from_tools(self):
        cfg = _make_config("fs")
        tools = [_make_tool("read_file"), _make_tool("write_file")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)
        assert "read_file" in agent.manifest.description
        assert "write_file" in agent.manifest.description

    def test_description_override(self):
        cfg = MCPServerConfig(
            name="custom",
            transport="stdio",
            command="python",
            description_override="My custom file server",
        )
        tools = [_make_tool("read")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)
        assert agent.manifest.description == "My custom file server"

    def test_examples_generated(self):
        cfg = _make_config("fs")
        tools = [_make_tool("read_file", "Read a file from disk")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)
        assert len(agent.manifest.examples) >= 1
        assert "read a file from disk" in agent.manifest.examples[0].lower()

    def test_model_tier_is_none(self):
        cfg = _make_config()
        tools = [_make_tool("tool1")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)
        assert agent.manifest.model_tier == "none"

    def test_requires_confirmation_true(self):
        cfg = _make_config()
        tools = [_make_tool("tool1")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)
        assert agent.manifest.requires_confirmation is True


class TestToolSelection:
    def test_select_by_hint(self):
        cfg = _make_config()
        tools = [
            _make_tool("read_file", "Read a file"),
            _make_tool("write_file", "Write a file"),
        ]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        tool, args = agent._select_tool("anything", "write_file", {})
        assert tool.name == "write_file"

    def test_select_by_hint_case_insensitive(self):
        cfg = _make_config()
        tools = [_make_tool("Read_File", "Read a file")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        tool, args = agent._select_tool("anything", "read_file", {})
        assert tool.name == "Read_File"

    def test_select_by_word_overlap(self):
        cfg = _make_config()
        tools = [
            _make_tool("read_file", "Read a file from disk"),
            _make_tool("list_directory", "List files in a directory"),
        ]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        tool, args = agent._select_tool("list the directory contents", None, {})
        assert tool.name == "list_directory"

    def test_select_single_tool_fallback(self):
        cfg = _make_config()
        tools = [_make_tool("only_tool", "Does something")]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        tool, args = agent._select_tool("completely unrelated query", None, {})
        assert tool.name == "only_tool"

    def test_select_no_match(self):
        cfg = _make_config()
        tools = [
            _make_tool("read_file", "Read a file"),
            _make_tool("write_file", "Write a file"),
        ]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        tool, args = agent._select_tool("weather forecast tomorrow", None, {})
        # With 2+ tools and no overlap, should return None
        assert tool is None


class TestBuildArguments:
    def test_params_fill_schema_properties(self):
        tool = _make_tool("read_file", "Read", {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "encoding": {"type": "string"},
            },
            "required": ["path"],
        })
        args = MCPAgent._build_arguments(tool, "read /tmp/test", {"path": "/tmp/test"})
        assert args["path"] == "/tmp/test"
        assert "encoding" not in args

    def test_required_string_fallback_to_query(self):
        tool = _make_tool("search", "Search", {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        })
        args = MCPAgent._build_arguments(tool, "find recent invoices", {})
        assert args["query"] == "find recent invoices"

    def test_non_required_params_omitted(self):
        tool = _make_tool("read_file", "Read", {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "encoding": {"type": "string"},
            },
            "required": ["path"],
        })
        args = MCPAgent._build_arguments(tool, "read something", {})
        assert "path" in args
        assert "encoding" not in args


class TestExecute:
    def test_execute_success(self):
        cfg = _make_config("fs")
        tools = [_make_tool("read_file", "Read a file from disk", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        # Mock the sync bridge to return success
        with patch(
            "giva.agents.mcp_agent.agent.run_mcp_coro",
            return_value=(True, "file contents: hello world"),
        ):
            store = MagicMock()
            config = GivaConfig()
            result = agent.execute(
                "read the file /tmp/test.txt",
                {"params": {"path": "/tmp/test.txt"}},
                store, config,
            )

        assert result.success is True
        assert "hello world" in result.output
        assert result.actions[0]["type"] == "mcp_tool_called"
        assert result.artifacts["server"] == "fs"
        assert result.artifacts["tool"] == "read_file"

    def test_execute_tool_error(self):
        cfg = _make_config("fs")
        tools = [_make_tool("read_file", "Read a file", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        with patch(
            "giva.agents.mcp_agent.agent.run_mcp_coro",
            return_value=(False, "file not found"),
        ):
            store = MagicMock()
            config = GivaConfig()
            result = agent.execute(
                "read /nonexistent",
                {"params": {}},
                store, config,
            )

        assert result.success is False
        assert "file not found" in result.output

    def test_execute_no_tools(self):
        cfg = _make_config("empty")
        conn = MagicMock()
        agent = MCPAgent(cfg, [], conn)

        store = MagicMock()
        config = GivaConfig()
        result = agent.execute("do something", {}, store, config)

        assert result.success is False
        assert "no tools" in result.error.lower()

    def test_execute_exception_handled(self):
        cfg = _make_config("fs")
        tools = [_make_tool("read_file", "Read a file", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        with patch(
            "giva.agents.mcp_agent.agent.run_mcp_coro",
            side_effect=TimeoutError("connection timed out"),
        ):
            store = MagicMock()
            config = GivaConfig()
            result = agent.execute("read /tmp/test", {"params": {}}, store, config)

        assert result.success is False
        assert "timed out" in result.error


class TestGetToolsInfo:
    def test_returns_tool_metadata(self):
        cfg = _make_config()
        tools = [
            _make_tool("read_file", "Read a file", {"type": "object"}),
            _make_tool("write_file", "Write a file"),
        ]
        conn = MagicMock()
        agent = MCPAgent(cfg, tools, conn)

        info = agent.get_tools_info()
        assert len(info) == 2
        assert info[0]["name"] == "read_file"
        assert info[0]["description"] == "Read a file"
        assert info[0]["input_schema"] == {"type": "object"}
