"""MCPAgent: wraps an MCP server as a Giva pluggable agent.

Each MCPAgent instance corresponds to one configured MCP server.
The agent auto-generates its :class:`AgentManifest` from discovered tools.
Tool selection within ``execute()`` uses query–description word overlap
(no LLM call — MCPAgent never touches ``_llm_lock``).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from giva.agents.base import AgentManifest, AgentResult, BaseAgent
from giva.agents.mcp_agent.config import MCPServerConfig
from giva.agents.mcp_agent.lifecycle import MCPConnection, run_mcp_coro
from giva.config import GivaConfig
from giva.db.store import Store

log = logging.getLogger(__name__)

# Words too generic to help with tool selection
_TOOL_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "was", "one", "has", "how", "its", "may", "new", "now",
    "use", "with", "this", "that", "from", "have", "been", "will",
    "what", "when", "where", "which", "about", "could", "would",
    "should", "there", "their", "them", "then", "than", "some",
    "into", "other", "also", "just", "more", "make", "like", "time",
    "very", "your", "want", "need", "help", "please",
})


class MCPAgent(BaseAgent):
    """A Giva agent backed by an MCP server.

    One MCPAgent per configured MCP server.  Wraps the MCP SDK's
    :class:`ClientSession` to discover tools and call them.

    **Does NOT use the local LLM** — tool selection is heuristic.
    ``manifest.model_tier`` is ``"none"`` so the server knows not to
    acquire ``_llm_lock`` when calling this agent.
    """

    def __init__(
        self,
        server_config: MCPServerConfig,
        tools: list,
        connection: MCPConnection,
    ):
        self._server_config = server_config
        self._connection = connection
        self._tools = tools

        manifest = self._build_manifest(server_config, tools)
        super().__init__(manifest)

    # ---- manifest generation -------------------------------------------------

    @staticmethod
    def _build_manifest(
        config: MCPServerConfig, tools: list
    ) -> AgentManifest:
        """Generate an :class:`AgentManifest` from server config + discovered tools."""
        tool_names = [t.name for t in tools]

        # Description
        description = config.description_override
        if not description:
            if tool_names:
                desc_parts = ", ".join(tool_names[:5])
                if len(tool_names) > 5:
                    desc_parts += f" (+{len(tool_names) - 5} more)"
                description = (
                    f"MCP server ({config.name}) providing tools: {desc_parts}"
                )
            else:
                description = f"MCP server: {config.name}"

        # Examples from tool descriptions
        examples: list[str] = []
        for t in tools[:5]:
            if t.description:
                examples.append(
                    f"Use {config.name} to {t.description.lower().rstrip('.')}"
                )
            else:
                examples.append(f"Use {config.name} {t.name}")

        return AgentManifest(
            agent_id=f"mcp_{config.name}",
            name=f"MCP: {config.name.replace('_', ' ').title()}",
            description=description,
            examples=examples,
            model_tier="none",            # does NOT use local LLM
            supports_streaming=False,
            requires_confirmation=True,   # MCP tools can have side effects
            version="0.1.0",
        )

    # ---- execute -------------------------------------------------------------

    def execute(
        self,
        query: str,
        context: dict,
        store: Store,
        config: GivaConfig,
    ) -> AgentResult:
        """Execute the most relevant MCP tool for the given query.

        Uses ``run_mcp_coro()`` to bridge from sync into the dedicated
        MCP event loop.

        **IMPORTANT**: This method does NOT acquire ``_llm_lock``.  It
        makes no local LLM calls.  The server must NOT hold the lock
        when calling this agent.
        """
        if not self._tools:
            return AgentResult(
                success=False,
                output="",
                error=f"MCP server {self._server_config.name} has no tools available",
            )

        # Extract tool-name hint from router params if present
        params = context.get("params", {})
        tool_hint = params.get("tool_name") or params.get("tool")

        # Select the best tool
        tool, arguments = self._select_tool(query, tool_hint, params)
        if tool is None:
            return AgentResult(
                success=False,
                output="",
                error=(
                    f"No matching tool found on {self._server_config.name} "
                    f"for: {query}"
                ),
            )

        self.log.info(
            "Calling MCP tool: %s.%s", self._server_config.name, tool.name
        )

        # Bridge to async MCP call
        try:
            success, output = run_mcp_coro(
                self._connection.call_tool(tool.name, arguments),
                timeout=self._server_config.timeout_seconds + 5,
            )
        except Exception as exc:
            self.log.error("MCP tool call failed: %s", exc)
            return AgentResult(
                success=False, output="", error=f"MCP tool {tool.name} failed: {exc}",
            )

        return AgentResult(
            success=success,
            output=output,
            actions=[{
                "type": "mcp_tool_called",
                "server": self._server_config.name,
                "tool": tool.name,
                "arguments": arguments,
            }],
            artifacts={
                "server": self._server_config.name,
                "tool": tool.name,
                "arguments": arguments,
                "raw_output": output[:2000],
            },
        )

    # ---- tool selection ------------------------------------------------------

    def _select_tool(
        self,
        query: str,
        tool_hint: Optional[str],
        params: dict,
    ) -> tuple:
        """Select the best tool and build its arguments.

        Priority:
        1. Exact ``tool_hint`` match from router ``extracted_params``.
        2. Word-overlap scoring between query and tool descriptions.
        3. Single-tool fallback (if only one tool exists).

        Returns ``(tool, arguments)`` or ``(None, {})`` if no match.
        """
        # 1. Direct hint match
        if tool_hint:
            for t in self._tools:
                if t.name == tool_hint or t.name.lower() == tool_hint.lower():
                    return t, self._build_arguments(t, query, params)

        # 2. Word overlap scoring
        query_words = self._tokenize(query)

        best_tool = None
        best_score = 0
        for t in self._tools:
            tool_text = f"{t.name} {t.description or ''}"
            tool_words = self._tokenize(tool_text)
            score = len(query_words & tool_words)
            if score > best_score:
                best_score = score
                best_tool = t

        # 3. Single-tool fallback
        if best_tool is None and len(self._tools) == 1:
            best_tool = self._tools[0]

        if best_tool is None:
            return None, {}

        return best_tool, self._build_arguments(best_tool, query, params)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Split text into lowercase tokens, dropping stop words and short words."""
        return {
            w.lower()
            for w in re.split(r"\W+", text)
            if len(w) > 2 and w.lower() not in _TOOL_STOP_WORDS
        }

    @staticmethod
    def _build_arguments(tool, query: str, params: dict) -> dict:
        """Build tool arguments from the tool's input schema and available params.

        Strategy:
        - If ``params`` contain keys matching schema properties, use them.
        - For any required string params not provided, pass the raw query.
        - Non-required params without values are omitted.
        """
        schema = getattr(tool, "inputSchema", None) or {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        arguments: dict = {}
        for prop_name, prop_schema in properties.items():
            if prop_name in params:
                arguments[prop_name] = params[prop_name]
            elif prop_name in required:
                prop_type = prop_schema.get("type", "string")
                if prop_type == "string":
                    arguments[prop_name] = query

        return arguments

    # ---- introspection -------------------------------------------------------

    def get_tools_info(self) -> list[dict]:
        """Return tool metadata for debugging / API introspection."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": getattr(t, "inputSchema", {}),
            }
            for t in self._tools
        ]

    @property
    def connection(self) -> MCPConnection:
        """The underlying MCP connection (for lifecycle management)."""
        return self._connection
