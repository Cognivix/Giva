"""Optional MCP dependency guard.

Provides ``_HAS_MCP`` flag and re-exports of key MCP SDK classes.
When the ``mcp`` package is not installed, all names are set to ``None``
and ``agent_factory()`` returns an empty list — no import errors, no crashes.
"""

from __future__ import annotations

_HAS_MCP = False

# Re-exported MCP SDK types (None when mcp is not installed)
ClientSession = None
StdioServerParameters = None
stdio_client = None
streamable_http_client = None

try:
    from mcp import ClientSession, StdioServerParameters  # noqa: F401, F811
    from mcp.client.stdio import stdio_client  # noqa: F401, F811
    from mcp.client.streamable_http import streamable_http_client  # noqa: F401, F811

    _HAS_MCP = True
except ImportError:
    pass
