"""Tests for MCP observation gathering module."""

import json
from unittest.mock import MagicMock, patch

from giva.intelligence.mcp_observations import (
    _call_mcp_tool,
    _get_mcp_connection,
    fetch_relevant_notes,
    gather_all_mcp_observations,
    gather_discord_observations,
    gather_messages_observations,
    gather_notes_observations,
)


# --- _get_mcp_connection ---


@patch("giva.agents.registry.registry")
def test_get_mcp_connection_no_agent(mock_registry):
    """Should return None when agent is not registered."""
    mock_registry.get.return_value = None
    assert _get_mcp_connection("notes") is None


@patch("giva.agents.registry.registry")
def test_get_mcp_connection_success(mock_registry):
    """Should return the MCPConnection from the agent."""
    mock_conn = MagicMock()
    mock_agent = MagicMock()
    mock_agent._connection = mock_conn
    mock_registry.get.return_value = mock_agent
    assert _get_mcp_connection("notes") is mock_conn


@patch("giva.agents.registry.registry")
def test_get_mcp_connection_no_connection_attr(mock_registry):
    """Should return None when agent has no _connection."""
    mock_agent = MagicMock(spec=[])  # no _connection attribute
    mock_registry.get.return_value = mock_agent
    assert _get_mcp_connection("notes") is None


# --- _call_mcp_tool ---


@patch("giva.intelligence.mcp_observations._get_mcp_connection")
def test_call_mcp_tool_no_connection(mock_get_conn):
    """Should return None when no connection available."""
    mock_get_conn.return_value = None
    assert _call_mcp_tool("notes", "list-folders", {}) is None


@patch("giva.agents.mcp_agent.lifecycle.run_mcp_coro")
@patch("giva.intelligence.mcp_observations._get_mcp_connection")
def test_call_mcp_tool_success(mock_get_conn, mock_run):
    """Should return output on success."""
    mock_conn = MagicMock()
    mock_get_conn.return_value = mock_conn
    mock_run.return_value = (True, "tool output")
    result = _call_mcp_tool("notes", "list-folders", {})
    assert result == "tool output"


@patch("giva.agents.mcp_agent.lifecycle.run_mcp_coro")
@patch("giva.intelligence.mcp_observations._get_mcp_connection")
def test_call_mcp_tool_error(mock_get_conn, mock_run):
    """Should return None on tool error."""
    mock_conn = MagicMock()
    mock_get_conn.return_value = mock_conn
    mock_run.return_value = (False, "error message")
    assert _call_mcp_tool("notes", "list-folders", {}) is None


@patch("giva.agents.mcp_agent.lifecycle.run_mcp_coro")
@patch("giva.intelligence.mcp_observations._get_mcp_connection")
def test_call_mcp_tool_exception(mock_get_conn, mock_run):
    """Should return None on exception."""
    mock_conn = MagicMock()
    mock_get_conn.return_value = mock_conn
    mock_run.side_effect = TimeoutError("timed out")
    assert _call_mcp_tool("notes", "list-folders", {}) is None


# --- gather_notes_observations ---


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_notes_no_agent(mock_call):
    """Should return empty string when notes agent unavailable."""
    mock_call.return_value = None
    assert gather_notes_observations() == ""


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_notes_with_json_data(mock_call):
    """Should parse JSON folder and note data."""
    folders_json = json.dumps([
        {"name": "Notes"}, {"name": "Work"}, {"name": "Personal"}
    ])
    notes_json = json.dumps([
        {"title": "Meeting notes"},
        {"title": "Shopping list"},
        {"title": "Project plan"},
    ])
    mock_call.side_effect = [folders_json, notes_json]

    result = gather_notes_observations()
    assert "Apple Notes:" in result
    assert "3 folders" in result
    assert "Meeting notes" in result
    assert "Shopping list" in result


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_notes_with_text_output(mock_call):
    """Should handle non-JSON text output gracefully."""
    mock_call.side_effect = ["Notes\nWork\nPersonal", "Note 1\nNote 2"]

    result = gather_notes_observations()
    assert "Apple Notes:" in result


# --- gather_messages_observations ---


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_messages_no_agent(mock_call):
    """Should return empty string when messages agent unavailable."""
    mock_call.return_value = None
    assert gather_messages_observations() == ""


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_messages_with_unread(mock_call):
    """Should report unread count."""
    mock_call.return_value = json.dumps([
        {"sender": "Alice", "text": "Hey"},
        {"sender": "Bob", "text": "Meeting?"},
    ])
    result = gather_messages_observations()
    assert "iMessages:" in result
    assert "2 unread messages" in result


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_messages_no_unread(mock_call):
    """Should return empty when no unread messages."""
    mock_call.return_value = json.dumps([])
    assert gather_messages_observations() == ""


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_messages_singular(mock_call):
    """Should use singular form for 1 message."""
    mock_call.return_value = json.dumps([{"sender": "Alice", "text": "Hi"}])
    result = gather_messages_observations()
    assert "1 unread message" in result
    assert "messages" not in result  # should be singular


# --- gather_discord_observations ---


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_discord_no_agent(mock_call):
    """Should return empty string when discord agent unavailable."""
    mock_call.return_value = None
    assert gather_discord_observations() == ""


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_discord_with_channels(mock_call):
    """Should report DM channel count and names."""
    mock_call.return_value = json.dumps([
        {"name": "Alice", "recipient": {"username": "alice"}},
        {"name": "Bob", "recipient": {"username": "bob"}},
    ])
    result = gather_discord_observations()
    assert "Discord:" in result
    assert "2 active DM channels" in result


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_gather_discord_empty(mock_call):
    """Should return empty when no DM channels."""
    mock_call.return_value = json.dumps([])
    assert gather_discord_observations() == ""


# --- gather_all_mcp_observations ---


@patch("giva.intelligence.mcp_observations.gather_discord_observations")
@patch("giva.intelligence.mcp_observations.gather_messages_observations")
@patch("giva.intelligence.mcp_observations.gather_notes_observations")
def test_gather_all_combines_results(mock_notes, mock_msgs, mock_discord):
    """Should combine all non-empty observations."""
    mock_notes.return_value = "Apple Notes:\n  3 folders"
    mock_msgs.return_value = "iMessages: 2 unread messages"
    mock_discord.return_value = "Discord: 1 active DM channel"

    result = gather_all_mcp_observations()
    assert "Connected services:" in result
    assert "Apple Notes:" in result
    assert "iMessages:" in result
    assert "Discord:" in result


@patch("giva.intelligence.mcp_observations.gather_discord_observations")
@patch("giva.intelligence.mcp_observations.gather_messages_observations")
@patch("giva.intelligence.mcp_observations.gather_notes_observations")
def test_gather_all_empty_when_no_sources(mock_notes, mock_msgs, mock_discord):
    """Should return empty when all sources are empty."""
    mock_notes.return_value = ""
    mock_msgs.return_value = ""
    mock_discord.return_value = ""

    assert gather_all_mcp_observations() == ""


@patch("giva.intelligence.mcp_observations.gather_discord_observations")
@patch("giva.intelligence.mcp_observations.gather_messages_observations")
@patch("giva.intelligence.mcp_observations.gather_notes_observations")
def test_gather_all_partial_sources(mock_notes, mock_msgs, mock_discord):
    """Should include only non-empty sources."""
    mock_notes.return_value = "Apple Notes:\n  3 folders"
    mock_msgs.return_value = ""
    mock_discord.return_value = ""

    result = gather_all_mcp_observations()
    assert "Apple Notes:" in result
    assert "iMessages" not in result
    assert "Discord" not in result


@patch("giva.intelligence.mcp_observations.gather_discord_observations")
@patch("giva.intelligence.mcp_observations.gather_messages_observations")
@patch("giva.intelligence.mcp_observations.gather_notes_observations")
def test_gather_all_handles_exception(mock_notes, mock_msgs, mock_discord):
    """Should handle exceptions in individual gather functions."""
    mock_notes.side_effect = RuntimeError("boom")
    mock_msgs.return_value = "iMessages: 1 unread message"
    mock_discord.return_value = ""

    result = gather_all_mcp_observations()
    assert "iMessages:" in result
    # Should not crash despite notes error


# --- fetch_relevant_notes ---


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_fetch_relevant_notes_no_agent(mock_call):
    """Should return empty when notes agent unavailable."""
    mock_call.return_value = None
    assert fetch_relevant_notes("meeting") == ""


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_fetch_relevant_notes_success(mock_call):
    """Should return note content on success."""
    mock_call.return_value = "Meeting Notes:\n- Discussed Q4 roadmap\n- Action items assigned"
    result = fetch_relevant_notes("meeting", budget=500)
    assert "Meeting Notes:" in result
    assert "Q4 roadmap" in result


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_fetch_relevant_notes_budget_truncation(mock_call):
    """Should truncate to budget."""
    long_content = "A" * 5000
    mock_call.return_value = long_content
    result = fetch_relevant_notes("test", budget=10)  # 10 tokens = ~40 chars
    assert len(result) < len(long_content)
    assert "[...truncated]" in result


@patch("giva.intelligence.mcp_observations._call_mcp_tool")
def test_fetch_relevant_notes_empty_result(mock_call):
    """Should return empty for whitespace-only results."""
    mock_call.return_value = "   \n  "
    assert fetch_relevant_notes("test") == ""
