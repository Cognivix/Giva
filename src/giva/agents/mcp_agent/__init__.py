"""MCP Agent: wraps MCP servers as pluggable Giva agents.

Uses ``agent_factory()`` (Convention 2 in the registry) instead of
``AGENT_CLASS`` because MCP creates multiple agent instances — one per
configured server.

Returns an empty list when:
- ``mcp`` package is not installed (optional dependency)
- No MCP servers are configured
- All configured servers fail to connect at startup
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def agent_factory():
    """Discover configured MCP servers and return MCPAgent instances.

    Called by :meth:`AgentRegistry.discover` at server startup.
    Returns a list of agent instances (possibly empty).
    """
    from giva.agents.mcp_agent._compat import _HAS_MCP

    if not _HAS_MCP:
        log.debug("MCP package not installed — MCP agents disabled")
        return []

    from giva.agents.mcp_agent.agent import MCPAgent
    from giva.agents.mcp_agent.config import load_mcp_servers
    from giva.agents.mcp_agent.lifecycle import MCPConnection, run_mcp_coro
    from giva.config import load_raw_config

    raw = load_raw_config()
    server_configs = load_mcp_servers(raw)
    if not server_configs:
        log.debug("No MCP servers configured")
        return []

    # Connect to each server (async, bridged via run_mcp_coro)
    agents: list[MCPAgent] = []

    async def _init_servers():
        results = []
        for sc in server_configs:
            conn = MCPConnection(sc)
            ok = await conn.connect()
            if ok:
                results.append((sc, conn))
            else:
                log.warning("MCP server %s failed to connect, skipping", sc.name)
        return results

    try:
        connected = run_mcp_coro(_init_servers(), timeout=120)
    except Exception as exc:
        log.warning("MCP server initialization failed: %s", exc)
        return []

    for sc, conn in connected:
        agent = MCPAgent(sc, conn.tools, conn)
        agents.append(agent)
        log.info(
            "MCP agent created: mcp_%s (%d tools)", sc.name, len(conn.tools),
        )

    return agents
