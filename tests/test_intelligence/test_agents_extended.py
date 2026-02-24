"""Extended tests for the post-chat agent pipeline.

Tests cover:
- _handle_create_task (with goal auto-linking and duplicate detection)
- _handle_create_objective (with tier inference)
- _handle_complete_task (title matching)
- _handle_preference (fact storage)
- _auto_link_goal (keyword overlap matching)
- run_post_chat_agent (full pipeline with mocked LLM)
"""

from unittest.mock import patch

from giva.db.models import Goal, Task, UserProfile
from giva.intelligence.agents import (
    _auto_link_goal,
    _handle_complete_task,
    _handle_create_objective,
    _handle_create_task,
    _handle_preference,
    run_post_chat_agent,
)


class TestHandleCreateTask:

    def test_creates_task(self, tmp_db):
        intent = {"title": "Buy groceries", "description": "Milk and eggs", "priority": "low"}
        action = _handle_create_task(intent, tmp_db)
        assert action is not None
        assert action["type"] == "task_created"
        assert action["title"] == "Buy groceries"
        assert action["priority"] == "low"

        # Verify DB
        tasks = tmp_db.get_tasks(status="pending")
        assert len(tasks) == 1
        assert tasks[0].source_type == "chat"

    def test_skips_duplicate_title(self, tmp_db):
        tmp_db.add_task(Task(title="Buy groceries", source_type="chat", source_id=0))
        intent = {"title": "Buy groceries"}
        action = _handle_create_task(intent, tmp_db)
        assert action is None

    def test_case_insensitive_duplicate(self, tmp_db):
        tmp_db.add_task(Task(title="Buy Groceries", source_type="chat", source_id=0))
        intent = {"title": "buy groceries"}
        action = _handle_create_task(intent, tmp_db)
        assert action is None

    def test_no_title_returns_none(self, tmp_db):
        intent = {"description": "Something"}
        action = _handle_create_task(intent, tmp_db)
        assert action is None

    def test_auto_links_to_goal_by_context(self, tmp_db):
        goal_id = tmp_db.add_goal(Goal(title="Health fitness", tier="long_term"))
        intent = {"title": "Go to gym"}
        action = _handle_create_task(intent, tmp_db, goal_id=goal_id)
        assert action is not None
        assert action["goal_id"] == goal_id

    def test_default_priority_medium(self, tmp_db):
        intent = {"title": "Test task"}
        action = _handle_create_task(intent, tmp_db)
        assert action["priority"] == "medium"


class TestHandleCreateObjective:

    def test_creates_objective(self, tmp_db):
        intent = {"title": "Launch MVP", "description": "First version", "tier": "mid_term"}
        action = _handle_create_objective(intent, tmp_db)
        assert action is not None
        assert action["type"] == "objective_created"
        assert action["title"] == "Launch MVP"
        assert action["tier"] == "mid_term"

    def test_infers_tier_from_long_term_parent(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Career growth", tier="long_term"))
        intent = {"title": "Get promotion"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action["tier"] == "mid_term"
        assert action["parent_id"] == parent_id

    def test_infers_tier_from_mid_term_parent(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Launch product", tier="mid_term"))
        intent = {"title": "Write docs"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action["tier"] == "short_term"

    def test_short_term_parent_detaches(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Quick task", tier="short_term"))
        intent = {"title": "Sub item"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action is not None
        assert action["parent_id"] is None

    def test_skips_duplicate_child(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Career", tier="long_term"))
        tmp_db.add_goal(Goal(
            title="Get promotion", tier="mid_term", parent_id=parent_id,
        ))
        intent = {"title": "Get promotion"}
        action = _handle_create_objective(intent, tmp_db, goal_id=parent_id)
        assert action is None

    def test_no_title_returns_none(self, tmp_db):
        intent = {"description": "Something"}
        action = _handle_create_objective(intent, tmp_db)
        assert action is None


class TestAutoLinkGoal:

    def test_links_by_keyword_overlap(self, tmp_db):
        goal_id = tmp_db.add_goal(Goal(
            title="Learn Python programming", tier="long_term", category="education",
        ))
        result = _auto_link_goal("Practice Python exercises", "", tmp_db)
        assert result == goal_id

    def test_no_overlap_returns_none(self, tmp_db):
        tmp_db.add_goal(Goal(title="Learn cooking", tier="long_term"))
        result = _auto_link_goal("Fix car engine", "", tmp_db)
        assert result is None

    def test_no_goals_returns_none(self, tmp_db):
        result = _auto_link_goal("Some task", "", tmp_db)
        assert result is None

    def test_best_match_wins(self, tmp_db):
        tmp_db.add_goal(Goal(title="Learn Python", tier="long_term"))
        goal2 = tmp_db.add_goal(Goal(
            title="Master Python data science", tier="long_term", category="data",
        ))
        result = _auto_link_goal("Python data analysis project", "", tmp_db)
        assert result == goal2

    def test_short_words_ignored(self, tmp_db):
        tmp_db.add_goal(Goal(title="Do it", tier="short_term"))
        result = _auto_link_goal("Do it now", "", tmp_db)
        assert result is None  # Words "do", "it" are <=2 chars


class TestHandleCompleteTask:

    def test_completes_matching_task(self, tmp_db):
        t_id = tmp_db.add_task(Task(
            title="Review the report", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_tasks(status="pending")
        intent = {"title": "review the report"}
        action = _handle_complete_task(intent, tmp_db, tasks)
        assert action is not None
        assert action["type"] == "task_completed"
        assert action["task_id"] == t_id

    def test_partial_title_match(self, tmp_db):
        t_id = tmp_db.add_task(Task(
            title="Review Q3 financial report", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_tasks(status="pending")
        intent = {"title": "review q3 financial report"}
        action = _handle_complete_task(intent, tmp_db, tasks)
        assert action is not None

    def test_no_match_returns_none(self, tmp_db):
        tmp_db.add_task(Task(
            title="Completely different task", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_tasks(status="pending")
        intent = {"title": "something unrelated xyz"}
        action = _handle_complete_task(intent, tmp_db, tasks)
        assert action is None

    def test_empty_title_returns_none(self, tmp_db):
        intent = {"title": ""}
        action = _handle_complete_task(intent, tmp_db, [])
        assert action is None


class TestHandlePreference:

    def test_saves_new_preference(self, tmp_db):
        profile = UserProfile(
            display_name="Test", email_address="t@t.com",
            profile_data={"learned_facts": []},
        )
        tmp_db.upsert_profile(profile)

        intent = {"detail": "Prefers morning meetings"}
        action = _handle_preference(intent, tmp_db)
        assert action is not None
        assert action["type"] == "preference_saved"
        assert action["detail"] == "Prefers morning meetings"

        # Verify stored
        updated = tmp_db.get_profile()
        assert "Prefers morning meetings" in updated.profile_data["learned_facts"]

    def test_skips_duplicate_preference(self, tmp_db):
        profile = UserProfile(
            display_name="Test", email_address="t@t.com",
            profile_data={"learned_facts": ["Prefers morning meetings"]},
        )
        tmp_db.upsert_profile(profile)

        intent = {"detail": "Prefers morning meetings"}
        action = _handle_preference(intent, tmp_db)
        assert action is None

    def test_no_profile_returns_none(self, tmp_db):
        intent = {"detail": "Some preference"}
        action = _handle_preference(intent, tmp_db)
        assert action is None

    def test_empty_detail_returns_none(self, tmp_db):
        profile = UserProfile(
            display_name="Test", email_address="t@t.com", profile_data={},
        )
        tmp_db.upsert_profile(profile)
        intent = {"detail": ""}
        action = _handle_preference(intent, tmp_db)
        assert action is None


class TestRunPostChatAgent:

    @patch("giva.llm.engine.manager")
    def test_creates_task_from_llm_response(self, mock_manager, tmp_db, config):
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "create_task", "title": "Send invoice", '
            '"description": "Due Friday", "priority": "high"}], '
            '"topic": "billing", "progress": []}'
        )
        actions = run_post_chat_agent(
            "I need to send the invoice", "Sure, I'll help.", tmp_db, config,
        )
        assert len(actions) == 1
        assert actions[0]["type"] == "task_created"
        assert actions[0]["title"] == "Send invoice"

    @patch("giva.llm.engine.manager")
    def test_logs_progress(self, mock_manager, tmp_db, config):
        goal_id = tmp_db.add_goal(Goal(title="Ship v2", tier="mid_term"))
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "none"}], "topic": "progress", '
            f'"progress": [{{"goal_id": {goal_id}, "note": "Finished API"}}]}}'
        )
        actions = run_post_chat_agent(
            "I finished the API", "Great job!", tmp_db, config,
        )
        assert any(a["type"] == "goal_progress" for a in actions)

    @patch("giva.llm.engine.manager")
    def test_handles_llm_error_gracefully(self, mock_manager, tmp_db, config):
        mock_manager.generate.side_effect = RuntimeError("LLM crashed")
        actions = run_post_chat_agent(
            "test query", "test response", tmp_db, config,
        )
        assert actions == []

    @patch("giva.llm.engine.manager")
    def test_no_intents_returns_empty(self, mock_manager, tmp_db, config):
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "none"}], "topic": "chat", "progress": []}'
        )
        actions = run_post_chat_agent("hi", "hello!", tmp_db, config)
        assert actions == []

    @patch("giva.llm.engine.manager")
    def test_goal_scoped_auto_links(self, mock_manager, tmp_db, config):
        goal_id = tmp_db.add_goal(Goal(title="Health", tier="long_term"))
        mock_manager.generate.return_value = (
            '{"intents": [{"type": "create_task", "title": "Jog 5k"}], '
            '"topic": "exercise", "progress": []}'
        )
        actions = run_post_chat_agent(
            "I should jog 5k", "That's a great plan!", tmp_db, config,
            goal_id=goal_id,
        )
        assert len(actions) == 1
        assert actions[0]["goal_id"] == goal_id
