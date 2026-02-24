"""Tests for the reactive query handler."""

from unittest.mock import MagicMock, patch

from giva.db.models import UserProfile


class TestHandleQuery:

    @patch("giva.intelligence.queries.engine")
    @patch("giva.agents.registry.registry")
    def test_yields_tokens(self, mock_registry, mock_engine, tmp_db, config):
        """handle_query should yield each token from the LLM."""
        from giva.intelligence.queries import handle_query

        mock_registry.has_agents.return_value = False
        mock_engine.stream_generate.return_value = iter(["Hello", " ", "world"])

        tokens = list(handle_query("What is 2+2?", tmp_db, config))
        assert tokens == ["Hello", " ", "world"]

    @patch("giva.intelligence.queries.engine")
    @patch("giva.agents.registry.registry")
    def test_saves_messages_to_db(self, mock_registry, mock_engine, tmp_db, config):
        """Query and response should be saved to conversation history."""
        from giva.intelligence.queries import handle_query

        mock_registry.has_agents.return_value = False
        mock_engine.stream_generate.return_value = iter(["The answer is 4"])

        list(handle_query("What is 2+2?", tmp_db, config))

        messages = tmp_db.get_recent_messages(limit=10)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "What is 2+2?"
        assert messages[1]["role"] == "assistant"
        assert "The answer is 4" in messages[1]["content"]

    @patch("giva.intelligence.queries.engine")
    @patch("giva.agents.registry.registry")
    def test_goal_scoped_messages(self, mock_registry, mock_engine, tmp_db, config):
        """Goal-scoped queries save messages with goal_id."""
        from giva.db.models import Goal
        from giva.intelligence.queries import handle_query

        mock_registry.has_agents.return_value = False
        mock_engine.stream_generate.return_value = iter(["OK"])

        goal_id = tmp_db.add_goal(Goal(title="Test goal", tier="long_term"))
        list(handle_query("How's the goal?", tmp_db, config, goal_id=goal_id))

        # Global chat should be empty
        global_msgs = tmp_db.get_recent_messages(limit=10, goal_id=None)
        assert len(global_msgs) == 0

        # Goal-scoped chat should have messages
        goal_msgs = tmp_db.get_recent_messages(limit=10, goal_id=goal_id)
        assert len(goal_msgs) == 2

    @patch("giva.intelligence.queries.engine")
    @patch("giva.agents.registry.registry")
    def test_strips_think_tags_from_saved_response(
        self, mock_registry, mock_engine, tmp_db, config,
    ):
        """<think> tags should be stripped from the saved conversation."""
        from giva.intelligence.queries import handle_query

        mock_registry.has_agents.return_value = False
        mock_engine.stream_generate.return_value = iter([
            "<think>reasoning here</think>The answer is 42"
        ])

        list(handle_query("Test?", tmp_db, config))
        messages = tmp_db.get_recent_messages(limit=10)
        assistant_msg = [m for m in messages if m["role"] == "assistant"][0]
        assert "<think>" not in assistant_msg["content"]
        assert "42" in assistant_msg["content"]

    @patch("giva.intelligence.queries.engine")
    @patch("giva.agents.registry.registry")
    def test_context_prefix_not_saved(
        self, mock_registry, mock_engine, tmp_db, config,
    ):
        """context_prefix should be sent to LLM but not saved to DB."""
        from giva.intelligence.queries import handle_query

        mock_registry.has_agents.return_value = False
        mock_engine.stream_generate.return_value = iter(["Answer"])

        list(handle_query(
            "Progress?", tmp_db, config,
            context_prefix="Goal: Learn Python (active)",
        ))

        messages = tmp_db.get_recent_messages(limit=10)
        user_msg = [m for m in messages if m["role"] == "user"][0]
        assert user_msg["content"] == "Progress?"
        assert "Goal: Learn Python" not in user_msg["content"]

    @patch("giva.intelligence.queries.engine")
    @patch("giva.agents.registry.registry")
    def test_conversation_history_included(
        self, mock_registry, mock_engine, tmp_db, config,
    ):
        """Recent conversation history should be included in LLM messages."""
        from giva.intelligence.queries import handle_query

        mock_registry.has_agents.return_value = False
        mock_engine.stream_generate.return_value = iter(["Response"])

        # Add prior conversation
        tmp_db.add_message("user", "Hello")
        tmp_db.add_message("assistant", "Hi there!")

        list(handle_query("Follow up question", tmp_db, config))

        # Check that stream_generate was called with conversation history
        call_args = mock_engine.stream_generate.call_args
        messages = call_args[0][0]
        # Should have: system, prior user, prior assistant, new user
        assert len(messages) >= 4
        roles = [m["role"] for m in messages]
        assert "system" in roles
