"""Tests for MCP server configuration parsing."""

from __future__ import annotations

from giva.agents.mcp_agent.config import MCPServerConfig, load_mcp_servers


class TestMCPServerConfig:
    def test_validate_valid_stdio(self):
        cfg = MCPServerConfig(name="fs", transport="stdio", command="npx")
        assert cfg.validate() == []

    def test_validate_valid_http(self):
        cfg = MCPServerConfig(name="api", transport="http", url="http://localhost:8080/mcp")
        assert cfg.validate() == []

    def test_validate_unknown_transport(self):
        cfg = MCPServerConfig(name="x", transport="grpc")
        errors = cfg.validate()
        assert len(errors) == 1
        assert "grpc" in errors[0]

    def test_validate_stdio_missing_command(self):
        cfg = MCPServerConfig(name="x", transport="stdio")
        errors = cfg.validate()
        assert any("command" in e for e in errors)

    def test_validate_http_missing_url(self):
        cfg = MCPServerConfig(name="x", transport="http")
        errors = cfg.validate()
        assert any("url" in e for e in errors)


class TestLoadMCPServers:
    def test_parse_stdio_config(self):
        raw = {
            "mcp_servers": {
                "filesystem": {
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    "timeout_seconds": 45,
                }
            }
        }
        configs = load_mcp_servers(raw)
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg.name == "filesystem"
        assert cfg.transport == "stdio"
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        assert cfg.timeout_seconds == 45

    def test_parse_http_config(self):
        raw = {
            "mcp_servers": {
                "remote": {
                    "transport": "http",
                    "url": "http://localhost:8080/mcp",
                }
            }
        }
        configs = load_mcp_servers(raw)
        assert len(configs) == 1
        assert configs[0].transport == "http"
        assert configs[0].url == "http://localhost:8080/mcp"

    def test_skip_disabled_server(self):
        raw = {
            "mcp_servers": {
                "disabled_one": {
                    "transport": "stdio",
                    "command": "npx",
                    "enabled": False,
                }
            }
        }
        configs = load_mcp_servers(raw)
        assert len(configs) == 0

    def test_skip_invalid_transport(self):
        raw = {
            "mcp_servers": {
                "bad": {
                    "transport": "websocket",
                    "command": "npx",
                }
            }
        }
        configs = load_mcp_servers(raw)
        assert len(configs) == 0

    def test_empty_config(self):
        configs = load_mcp_servers({})
        assert configs == []

    def test_empty_mcp_servers_section(self):
        configs = load_mcp_servers({"mcp_servers": {}})
        assert configs == []

    def test_env_vars_passed_through(self):
        raw = {
            "mcp_servers": {
                "gh": {
                    "transport": "stdio",
                    "command": "npx",
                    "env": {"GITHUB_TOKEN": "secret123"},
                }
            }
        }
        configs = load_mcp_servers(raw)
        assert configs[0].env == {"GITHUB_TOKEN": "secret123"}

    def test_description_override(self):
        raw = {
            "mcp_servers": {
                "custom": {
                    "transport": "stdio",
                    "command": "python",
                    "description": "My custom MCP server for file operations",
                }
            }
        }
        configs = load_mcp_servers(raw)
        assert configs[0].description_override == "My custom MCP server for file operations"

    def test_non_dict_entry_skipped(self):
        raw = {
            "mcp_servers": {
                "bad_entry": "not a dict",
                "good_entry": {
                    "transport": "stdio",
                    "command": "python",
                },
            }
        }
        configs = load_mcp_servers(raw)
        assert len(configs) == 1
        assert configs[0].name == "good_entry"
