"""MCP server configuration: parsing and validation.

Reads ``[mcp_servers.<name>]`` sections from the merged TOML config dict
and returns validated :class:`MCPServerConfig` instances.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for a single MCP server.

    Each entry under ``[mcp_servers]`` in the TOML config becomes one
    instance. Invalid or disabled entries are skipped at parse time.
    """

    name: str                                          # server identifier (TOML key)
    transport: str                                     # "stdio" | "http"
    # Stdio-specific
    command: Optional[str] = None                      # e.g. "npx", "python"
    args: list[str] = field(default_factory=list)      # e.g. ["-y", "server-pkg"]
    # HTTP-specific
    url: Optional[str] = None                          # e.g. "http://localhost:8080/mcp"
    # Shared
    env: dict[str, str] = field(default_factory=dict)  # extra environment variables
    timeout_seconds: int = 30                          # per-call timeout
    enabled: bool = True                               # can disable without removing config
    description_override: Optional[str] = None         # custom description for manifest

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty means valid)."""
        errors: list[str] = []
        if self.transport not in ("stdio", "http"):
            errors.append(f"Unknown transport: {self.transport!r}")
        if self.transport == "stdio" and not self.command:
            errors.append("stdio transport requires 'command'")
        if self.transport == "http" and not self.url:
            errors.append("http transport requires 'url'")
        return errors


def load_mcp_servers(raw_config: dict) -> list[MCPServerConfig]:
    """Parse ``[mcp_servers]`` section from a raw TOML config dict.

    Returns a list of valid, enabled :class:`MCPServerConfig` instances.
    Invalid or disabled entries are logged and skipped.
    """
    servers_raw = raw_config.get("mcp_servers", {})
    configs: list[MCPServerConfig] = []

    for name, data in servers_raw.items():
        if not isinstance(data, dict):
            log.warning("mcp_servers.%s: expected table, got %s", name, type(data).__name__)
            continue

        try:
            cfg = MCPServerConfig(
                name=name,
                transport=data.get("transport", "stdio"),
                command=data.get("command"),
                args=data.get("args", []),
                url=data.get("url"),
                env=data.get("env", {}),
                timeout_seconds=int(data.get("timeout_seconds", 30)),
                enabled=data.get("enabled", True),
                description_override=data.get("description"),
            )
            errors = cfg.validate()
            if errors:
                for err in errors:
                    log.warning("mcp_servers.%s: %s", name, err)
                continue
            if not cfg.enabled:
                log.debug("mcp_servers.%s: disabled, skipping", name)
                continue
            configs.append(cfg)
        except Exception as exc:
            log.warning("mcp_servers.%s: parse error: %s", name, exc)

    return configs
