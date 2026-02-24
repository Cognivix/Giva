"""Tests for goal intelligence: summaries, initial goal creation, plan acceptance, and JSON parsing."""

from datetime import datetime
from unittest.mock import patch

from giva.db.models import Goal, Task
from giva.intelligence.goals import (
    _parse_json_array,
    _parse_json_response,
    accept_plan,
    create_initial_goals,
    get_goals_summary,
)
from giva.llm.structured import TacticalPlan


# --- get_goals_summary ---


class TestGetGoalsSummary:

    def test_empty_goals_returns_empty(self, tmp_db):
        assert get_goals_summary(tmp_db) == ""

    def test_single_goal(self, tmp_db):
        tmp_db.add_goal(Goal(title="Ship product", tier="long_term", priority="high"))
        summary = get_goals_summary(tmp_db)
        assert "Ship product" in summary
        assert "Long-term" in summary
        assert "[HIGH]" in summary

    def test_multiple_tiers(self, tmp_db):
        tmp_db.add_goal(Goal(title="Career growth", tier="long_term"))
        tmp_db.add_goal(Goal(title="Get promotion", tier="mid_term"))
        tmp_db.add_goal(Goal(title="Update resume", tier="short_term"))

        summary = get_goals_summary(tmp_db)
        assert "Long-term" in summary
        assert "Mid-term" in summary
        assert "Short-term" in summary

    def test_includes_child_count(self, tmp_db):
        parent_id = tmp_db.add_goal(Goal(title="Career", tier="long_term"))
        tmp_db.add_goal(Goal(title="Skill A", tier="mid_term", parent_id=parent_id))
        tmp_db.add_goal(Goal(title="Skill B", tier="mid_term", parent_id=parent_id))

        summary = get_goals_summary(tmp_db)
        assert "2 sub-objectives" in summary

    def test_includes_pending_task_count(self, tmp_db):
        goal_id = tmp_db.add_goal(Goal(title="Ship v2", tier="mid_term"))
        tmp_db.add_task(Task(
            title="Fix bugs", source_type="chat", source_id=0, goal_id=goal_id,
        ))
        tmp_db.add_task(Task(
            title="Write tests", source_type="chat", source_id=0, goal_id=goal_id,
        ))

        summary = get_goals_summary(tmp_db)
        assert "2 pending tasks" in summary

    def test_includes_progress_when_requested(self, tmp_db):
        goal_id = tmp_db.add_goal(Goal(title="Ship v2", tier="mid_term"))
        tmp_db.add_goal_progress(goal_id, "Fixed the login bug", "chat")

        summary = get_goals_summary(tmp_db, include_progress=True)
        assert "Fixed the login bug" in summary

    def test_excludes_non_active_goals(self, tmp_db):
        tmp_db.add_goal(Goal(title="Active goal", tier="long_term"))
        tmp_db.add_goal(Goal(title="Paused goal", tier="long_term", status="paused"))

        summary = get_goals_summary(tmp_db)
        assert "Active goal" in summary
        assert "Paused goal" not in summary


# --- create_initial_goals ---


class TestCreateInitialGoals:

    def test_creates_goals_from_profile(self, tmp_db):
        profile_data = {
            "initial_goals": [
                {"title": "Learn Spanish", "tier": "long_term", "category": "education"},
                {"title": "Run marathon", "tier": "mid_term", "category": "health"},
            ],
        }
        count = create_initial_goals(tmp_db, profile_data)
        assert count == 2

        goals = tmp_db.get_goals()
        assert len(goals) == 2
        titles = {g.title for g in goals}
        assert "Learn Spanish" in titles
        assert "Run marathon" in titles

    def test_skips_empty_titles(self, tmp_db):
        profile_data = {
            "initial_goals": [
                {"title": "Valid goal", "tier": "long_term"},
                {"title": "", "tier": "long_term"},
                {"title": "   ", "tier": "long_term"},
            ],
        }
        count = create_initial_goals(tmp_db, profile_data)
        assert count == 1

    def test_no_initial_goals_returns_zero(self, tmp_db):
        count = create_initial_goals(tmp_db, {})
        assert count == 0

    def test_defaults_tier_to_long_term(self, tmp_db):
        profile_data = {
            "initial_goals": [{"title": "Some goal"}],
        }
        create_initial_goals(tmp_db, profile_data)
        goals = tmp_db.get_goals()
        assert goals[0].tier == "long_term"


# --- accept_plan ---


class TestAcceptPlan:

    def test_creates_tasks_from_plan(self, tmp_db):
        plan_json = (
            '{"tasks": ['
            '{"title": "Write API docs", "description": "REST endpoints", '
            '"priority": "high", "due_date": "2026-03-15"},'
            '{"title": "Add tests", "priority": "medium"}'
            '], "email_drafts": [], "calendar_blocks": []}'
        )
        goal_id = tmp_db.add_goal(Goal(title="Ship v2", tier="mid_term"))
        count = accept_plan(plan_json, goal_id, tmp_db)
        assert count == 2

        tasks = tmp_db.get_tasks_for_goal(goal_id)
        assert len(tasks) == 2
        titles = {t.title for t in tasks}
        assert "Write API docs" in titles

    def test_invalid_json_returns_zero(self, tmp_db):
        goal_id = tmp_db.add_goal(Goal(title="Test", tier="mid_term"))
        count = accept_plan("not json at all", goal_id, tmp_db)
        assert count == 0

    def test_empty_tasks_returns_zero(self, tmp_db):
        plan_json = '{"tasks": [], "email_drafts": [], "calendar_blocks": []}'
        goal_id = tmp_db.add_goal(Goal(title="Test", tier="mid_term"))
        count = accept_plan(plan_json, goal_id, tmp_db)
        assert count == 0

    def test_invalid_due_date_handled(self, tmp_db):
        plan_json = (
            '{"tasks": [{"title": "Task", "priority": "low", '
            '"due_date": "not-a-date"}], "email_drafts": [], "calendar_blocks": []}'
        )
        goal_id = tmp_db.add_goal(Goal(title="Test", tier="mid_term"))
        count = accept_plan(plan_json, goal_id, tmp_db)
        assert count == 1  # Task created, due_date is None


# --- JSON parsing helpers ---


class TestParseJsonResponse:

    def test_parses_valid_json(self):
        response = '{"goals": [{"title": "Test", "tier": "long_term", "category": "test"}]}'
        from giva.llm.structured import GoalInferenceResult

        result = _parse_json_response(response, GoalInferenceResult)
        assert result is not None
        assert len(result.goals) == 1

    def test_extracts_json_from_surrounding_text(self):
        response = 'Here is the result:\n{"goals": []}\nDone.'
        from giva.llm.structured import GoalInferenceResult

        result = _parse_json_response(response, GoalInferenceResult)
        assert result is not None

    def test_returns_none_for_no_json(self):
        from giva.llm.structured import GoalInferenceResult

        result = _parse_json_response("no json here", GoalInferenceResult)
        assert result is None

    def test_returns_none_for_invalid_json(self):
        from giva.llm.structured import GoalInferenceResult

        result = _parse_json_response("{invalid json{", GoalInferenceResult)
        assert result is None


class TestParseJsonArray:

    def test_parses_valid_array(self):
        response = '[{"goal_id": 1, "note": "Progress"}]'
        result = _parse_json_array(response)
        assert len(result) == 1
        assert result[0]["goal_id"] == 1

    def test_empty_array(self):
        result = _parse_json_array("[]")
        assert result == []

    def test_no_array_returns_empty(self):
        result = _parse_json_array("no array here")
        assert result == []

    def test_extracts_from_surrounding_text(self):
        response = 'Here:\n[{"id": 1}]\nDone.'
        result = _parse_json_array(response)
        assert len(result) == 1
