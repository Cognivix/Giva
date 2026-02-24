"""MCP source observation gathering for onboarding and context retrieval.

Calls MCP servers (Notes, Messages, Discord) to gather observations that
feed into onboarding prompts and the context assembly pipeline.

Uses ``run_mcp_coro()`` to bridge sync→async.  All functions are fail-safe:
they return ``""`` on any error (missing agent, connection failure, etc.).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger(__name__)


def _get_mcp_connection(server_name: str):
    """Get the MCPConnection for a registered MCP agent, or None."""
    try:
        from giva.agents.registry import registry

        agent = registry.get(f"mcp_{server_name}")
        if agent is None:
            log.debug("MCP agent mcp_%s not registered", server_name)
            return None
        # Access the underlying connection
        conn = getattr(agent, "_connection", None)
        if conn is None:
            log.debug("MCP agent mcp_%s has no _connection attribute", server_name)
            return None
        return conn
    except Exception as exc:
        log.debug("Failed to get MCP connection for %s: %s", server_name, exc)
        return None


def _call_mcp_tool(server_name: str, tool_name: str, arguments: dict) -> Optional[str]:
    """Call an MCP tool and return its text output, or None on failure."""
    conn = _get_mcp_connection(server_name)
    if conn is None:
        return None

    try:
        from giva.agents.mcp_agent.lifecycle import run_mcp_coro

        success, output = run_mcp_coro(
            conn.call_tool(tool_name, arguments),
            timeout=15,
        )
        if success:
            return output
        log.debug("MCP tool %s.%s returned error: %s", server_name, tool_name, output[:200])
        return None
    except Exception as exc:
        log.debug("MCP tool %s.%s call failed: %s", server_name, tool_name, exc)
        return None


# ---------------------------------------------------------------------------
# Individual source observations
# ---------------------------------------------------------------------------


def gather_notes_observations() -> str:
    """Gather observations from Apple Notes via MCP.

    Calls ``list-folders`` and ``list-notes`` to report folder count
    and recent note titles.
    """
    try:
        # List folders
        folders_text = _call_mcp_tool("notes", "list-folders", {})
        folder_count = 0
        if folders_text:
            try:
                folders_data = json.loads(folders_text)
                if isinstance(folders_data, list):
                    folder_count = len(folders_data)
            except (json.JSONDecodeError, TypeError):
                # Try counting lines as fallback
                folder_count = len([ln for ln in folders_text.strip().split("\n") if ln.strip()])

        # List recent notes
        notes_text = _call_mcp_tool("notes", "list-notes", {})
        note_titles = []
        if notes_text:
            try:
                notes_data = json.loads(notes_text)
                if isinstance(notes_data, list):
                    for n in notes_data[:10]:
                        if isinstance(n, dict):
                            title = n.get("title") or n.get("name", "")
                            if title:
                                note_titles.append(title)
                        elif isinstance(n, str):
                            note_titles.append(n)
            except (json.JSONDecodeError, TypeError):
                # Parse text output
                for line in notes_text.strip().split("\n"):
                    line = line.strip().lstrip("- ")
                    if line:
                        note_titles.append(line)
                note_titles = note_titles[:10]

        if not folder_count and not note_titles:
            return ""

        lines = ["Apple Notes:"]
        if folder_count:
            lines.append(f"  {folder_count} folders")
        if note_titles:
            lines.append(f"  Recent notes: {', '.join(note_titles[:5])}")
            if len(note_titles) > 5:
                lines.append(f"  (+{len(note_titles) - 5} more)")
        return "\n".join(lines)

    except Exception as exc:
        log.debug("gather_notes_observations failed: %s", exc)
        return ""


def gather_messages_observations() -> str:
    """Gather observations from iMessages via MCP.

    Calls ``get_unread_imessages`` to report unread count.
    """
    try:
        result = _call_mcp_tool("messages", "get_unread_imessages", {"limit": 20})
        if result is None:
            return ""

        # Parse result to count unread
        count = 0
        try:
            data = json.loads(result)
            if isinstance(data, list):
                count = len(data)
            elif isinstance(data, dict):
                count = data.get("count", len(data.get("messages", [])))
        except (json.JSONDecodeError, TypeError):
            # Count non-empty lines as messages
            count = len([ln for ln in result.strip().split("\n") if ln.strip()])

        if count == 0:
            return ""
        return f"iMessages: {count} unread message{'s' if count != 1 else ''}"

    except Exception as exc:
        log.debug("gather_messages_observations failed: %s", exc)
        return ""


def gather_discord_observations() -> str:
    """Gather observations from Discord via MCP.

    Calls ``discord_get_dm_channels`` to report active DM count.
    """
    try:
        result = _call_mcp_tool("discord", "discord_get_dm_channels", {})
        if result is None:
            return ""

        # Parse DM channels
        count = 0
        names = []
        try:
            data = json.loads(result)
            if isinstance(data, list):
                count = len(data)
                for ch in data[:5]:
                    if isinstance(ch, dict):
                        name = (
                            ch.get("name")
                            or ch.get("recipient", {}).get("username", "")
                        )
                        if name:
                            names.append(name)
        except (json.JSONDecodeError, TypeError):
            # Count lines as channels
            lines = [ln for ln in result.strip().split("\n") if ln.strip()]
            count = len(lines)

        if count == 0:
            return ""

        line = f"Discord: {count} active DM channel{'s' if count != 1 else ''}"
        if names:
            line += f" (recent: {', '.join(names[:3])})"
        return line

    except Exception as exc:
        log.debug("gather_discord_observations failed: %s", exc)
        return ""


def gather_all_mcp_observations() -> str:
    """Gather observations from all MCP sources.

    Calls each gather function, joins non-empty results.
    Returns ``""`` if no MCP sources are available.
    """
    observations = []

    for gather_fn in (
        gather_notes_observations,
        gather_messages_observations,
        gather_discord_observations,
    ):
        try:
            result = gather_fn()
            if result:
                observations.append(result)
        except Exception as exc:
            log.debug("MCP observation gather failed: %s", exc)

    if not observations:
        return ""

    return "Connected services:\n" + "\n".join(observations)


# ---------------------------------------------------------------------------
# Context retrieval: notes search
# ---------------------------------------------------------------------------


def fetch_relevant_notes(query: str, budget: int = 500) -> str:
    """Search Apple Notes for content relevant to the query.

    Args:
        query: Search query string.
        budget: Token budget for the returned text.

    Returns formatted note content, or ``""`` if unavailable.
    """
    try:
        result = _call_mcp_tool("notes", "search-notes", {"query": query})
        if result is None:
            return ""

        # Truncate to budget
        from giva.intelligence.context import truncate_to_budget

        truncated = truncate_to_budget(result, budget)
        return truncated if truncated.strip() else ""

    except Exception as exc:
        log.debug("fetch_relevant_notes failed: %s", exc)
        return ""
