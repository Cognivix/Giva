"""Tests for the post-chat agent pipeline and intelligence agents.

Tests cover:
- Progress Aggregator (pure code, no LLM)
- Fact Extractor (mocked LLM)
- Stale Task Detector (mocked LLM)
- Weekly Reflection scheduling checks
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from giva.db.models import Goal, Task, UserProfile


# --- Progress Aggregator ---


class TestProgressAggregator:
    """Tests for aggregate_task_progress — pure code, no LLM."""

    def test_aggregate_progress_on_linked_task(self, tmp_db):
        """Completing a task linked to a goal logs progress."""
        from giva.intelligence.agents import aggregate_task_progress

        goal_id = tmp_db.add_goal(Goal(title="Ship v2", tier="mid_term"))
        t1 = tmp_db.add_task(Task(
            title="Write tests", source_type="chat", source_id=0, goal_id=goal_id,
        ))
        tmp_db.add_task(Task(
            title="Fix bugs", source_type="chat", source_id=0, goal_id=goal_id,
        ))
        # Complete the first task
        tmp_db.update_task_status(t1, "done")

        action = aggregate_task_progress(t1, tmp_db)
        assert action is not None
        assert action["type"] == "goal_progress"
        assert action["goal_id"] == goal_id
        assert action["tasks_done"] == 1
        assert action["tasks_total"] == 2
        assert "Write tests" in action["note"]

        # Verify progress was logged in the DB
        progress = tmp_db.get_goal_progress(goal_id)
        assert len(progress) == 1
        assert "1/2 tasks done" in progress[0].note
        assert progress[0].source == "task"

    def test_aggregate_no_goal_returns_none(self, tmp_db):
        """Tasks without a goal_id return None."""
        from giva.intelligence.agents import aggregate_task_progress

        t_id = tmp_db.add_task(Task(
            title="Orphan task", source_type="chat", source_id=0,
        ))
        tmp_db.update_task_status(t_id, "done")

        result = aggregate_task_progress(t_id, tmp_db)
        assert result is None

    def test_aggregate_nonexistent_task(self, tmp_db):
        """Nonexistent task ID returns None."""
        from giva.intelligence.agents import aggregate_task_progress

        result = aggregate_task_progress(9999, tmp_db)
        assert result is None

    def test_aggregate_all_tasks_done(self, tmp_db):
        """Completing the last task shows full completion."""
        from giva.intelligence.agents import aggregate_task_progress

        goal_id = tmp_db.add_goal(Goal(title="Finish project", tier="short_term"))
        tmp_db.add_task(Task(
            title="Task A", source_type="chat", source_id=0,
            goal_id=goal_id, status="done",
        ))
        t2 = tmp_db.add_task(Task(
            title="Task B", source_type="chat", source_id=0, goal_id=goal_id,
        ))
        tmp_db.update_task_status(t2, "done")

        action = aggregate_task_progress(t2, tmp_db)
        assert action is not None
        assert action["tasks_done"] == 2
        assert action["tasks_total"] == 2

    def test_aggregate_deleted_goal_returns_none(self, tmp_db):
        """If the goal was deleted, returns None."""
        from giva.intelligence.agents import aggregate_task_progress

        goal_id = tmp_db.add_goal(Goal(title="Deletable", tier="short_term"))
        t_id = tmp_db.add_task(Task(
            title="Linked task", source_type="chat", source_id=0,
            goal_id=goal_id,
        ))
        tmp_db.update_task_status(t_id, "done")

        # Delete the goal
        with tmp_db._conn() as conn:
            conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))

        result = aggregate_task_progress(t_id, tmp_db)
        assert result is None


# --- Fact Extractor ---


class TestFactExtractor:
    """Tests for extract_facts_from_session."""

    def _setup_profile_with_session(self, tmp_db, session_summary, facts=None):
        """Helper to create a profile with session summary."""
        pd = {"session_summary": session_summary}
        if facts:
            pd["learned_facts"] = facts
        profile = UserProfile(
            display_name="Test User",
            email_address="test@example.com",
            profile_data=pd,
        )
        tmp_db.upsert_profile(profile)

    @patch("giva.llm.engine.manager")
    def test_extracts_new_facts(self, mock_manager, tmp_db, config):
        """New facts from session summary are added to learned_facts."""
        from giva.intelligence.daily_review import extract_facts_from_session

        self._setup_profile_with_session(
            tmp_db,
            "User mentioned they prefer morning meetings and dislike Slack.",
        )

        mock_manager.generate.return_value = (
            '{"new_facts": ["Prefers morning meetings", '
            '"Dislikes Slack notifications"], "obsolete_facts": []}'
        )

        result = extract_facts_from_session(tmp_db, config)
        assert len(result["new"]) == 2
        assert "Prefers morning meetings" in result["new"]

        # Verify stored in DB
        profile = tmp_db.get_profile()
        facts = profile.profile_data.get("learned_facts", [])
        assert "Prefers morning meetings" in facts

    @patch("giva.llm.engine.manager")
    def test_removes_obsolete_facts(self, mock_manager, tmp_db, config):
        """Obsolete facts are removed from learned_facts."""
        from giva.intelligence.daily_review import extract_facts_from_session

        self._setup_profile_with_session(
            tmp_db,
            "User now says they switched to evening work schedule.",
            facts=["Prefers morning meetings", "Likes Python"],
        )

        mock_manager.generate.return_value = (
            '{"new_facts": ["Prefers evening work"], '
            '"obsolete_facts": ["morning meetings"]}'
        )

        result = extract_facts_from_session(tmp_db, config)
        assert len(result["new"]) == 1
        assert len(result["removed"]) == 1

        profile = tmp_db.get_profile()
        facts = profile.profile_data.get("learned_facts", [])
        assert "Prefers evening work" in facts
        assert "Prefers morning meetings" not in facts
        assert "Likes Python" in facts

    def test_no_session_summary_noop(self, tmp_db, config):
        """No session summary → no-op."""
        from giva.intelligence.daily_review import extract_facts_from_session

        self._setup_profile_with_session(tmp_db, "")
        result = extract_facts_from_session(tmp_db, config)
        assert result == {}

    def test_no_profile_noop(self, tmp_db, config):
        """No profile → no-op."""
        from giva.intelligence.daily_review import extract_facts_from_session

        result = extract_facts_from_session(tmp_db, config)
        assert result == {}

    @patch("giva.llm.engine.manager")
    def test_clears_session_summary_after_extraction(self, mock_manager, tmp_db, config):
        """Session summary is cleared after facts are extracted."""
        from giva.intelligence.daily_review import extract_facts_from_session

        self._setup_profile_with_session(
            tmp_db, "User prefers dark mode."
        )

        mock_manager.generate.return_value = (
            '{"new_facts": ["Prefers dark mode"], "obsolete_facts": []}'
        )

        extract_facts_from_session(tmp_db, config)

        profile = tmp_db.get_profile()
        assert profile.profile_data.get("session_summary", "") == ""

    @patch("giva.llm.engine.manager")
    def test_avoids_duplicate_facts(self, mock_manager, tmp_db, config):
        """Facts already in learned_facts are not duplicated."""
        from giva.intelligence.daily_review import extract_facts_from_session

        self._setup_profile_with_session(
            tmp_db,
            "User mentioned Python again.",
            facts=["Likes Python"],
        )

        mock_manager.generate.return_value = (
            '{"new_facts": ["Likes Python"], "obsolete_facts": []}'
        )

        result = extract_facts_from_session(tmp_db, config)
        assert result.get("new", []) == []

        profile = tmp_db.get_profile()
        facts = profile.profile_data.get("learned_facts", [])
        assert facts.count("Likes Python") == 1


# --- Stale Task Detector ---


class TestStaleTaskDetector:
    """Tests for detect_stale_tasks."""

    @patch("giva.llm.engine.manager")
    def test_detects_overdue_tasks(self, mock_manager, tmp_db, config):
        """Overdue tasks are detected and classified."""
        from giva.intelligence.daily_review import detect_stale_tasks

        goal_id = tmp_db.add_goal(Goal(title="Ship v2", tier="mid_term"))

        # Create an overdue task
        t_id = tmp_db.add_task(Task(
            title="Fix critical bug",
            source_type="chat",
            source_id=0,
            due_date=datetime.now() - timedelta(days=3),
            goal_id=goal_id,
        ))

        mock_manager.generate.return_value = (
            '{"tasks": [{"task_id": ' + str(t_id) + ', '
            '"action": "remind", "reason": "3 days overdue, high priority"}]}'
        )

        results = detect_stale_tasks(tmp_db, config)
        assert len(results) == 1
        assert results[0]["task_id"] == t_id
        assert results[0]["action"] == "remind"
        assert results[0]["title"] == "Fix critical bug"

    def test_no_stale_tasks_returns_empty(self, tmp_db, config):
        """No overdue tasks → no LLM call, empty result."""
        from giva.intelligence.daily_review import detect_stale_tasks

        # Create a task due tomorrow
        tmp_db.add_task(Task(
            title="Future task",
            source_type="chat",
            source_id=0,
            due_date=datetime.now() + timedelta(days=1),
        ))

        results = detect_stale_tasks(tmp_db, config)
        assert results == []

    @patch("giva.llm.engine.manager")
    def test_detects_orphan_old_tasks(self, mock_manager, tmp_db, config):
        """Tasks with no due date, older than 7 days, are considered stale."""
        from giva.intelligence.daily_review import detect_stale_tasks

        # We can't set created_at directly, so we use a raw insert
        with tmp_db._conn() as conn:
            conn.execute(
                """INSERT INTO tasks (title, source_type, source_id, status, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("Old orphan", "email", 1, "pending",
                 (datetime.now() - timedelta(days=10)).isoformat()),
            )
            row = conn.execute(
                "SELECT id FROM tasks WHERE title = 'Old orphan'"
            ).fetchone()
            task_id = row["id"]

        mock_manager.generate.return_value = (
            '{"tasks": [{"task_id": ' + str(task_id) + ', '
            '"action": "retire", "reason": "10 days old, no due date"}]}'
        )

        results = detect_stale_tasks(tmp_db, config)
        assert len(results) == 1
        assert results[0]["action"] == "retire"


# --- Weekly Reflection Schedule Check ---


class TestWeeklyReflection:
    """Tests for is_reflection_due."""

    def test_reflection_due_on_correct_day_and_hour(self, tmp_db, config):
        """Reflection is due on the configured day at the right hour."""
        from giva.intelligence.daily_review import is_reflection_due

        # Patch datetime to be Sunday at 19:00
        target_day = config.goals.weekly_reflection_day  # 6 = Sunday
        target_hour = config.goals.weekly_reflection_hour  # 18

        # Find the next Sunday
        now = datetime.now()
        days_ahead = target_day - now.weekday()
        if days_ahead < 0:
            days_ahead += 7
        next_sunday = now + timedelta(days=days_ahead)
        fake_now = next_sunday.replace(hour=target_hour + 1, minute=0, second=0)

        with patch("giva.intelligence.daily_review.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = is_reflection_due(tmp_db, config)
            assert result is True

    def test_reflection_not_due_wrong_day(self, tmp_db, config):
        """Reflection is not due on the wrong day."""
        from giva.intelligence.daily_review import is_reflection_due

        # Patch to a Monday at 19:00
        now = datetime.now()
        days_ahead = 0 - now.weekday()  # Monday
        if days_ahead <= 0:
            days_ahead += 7
        next_monday = now + timedelta(days=days_ahead)
        fake_now = next_monday.replace(hour=19, minute=0, second=0)

        with patch("giva.intelligence.daily_review.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = is_reflection_due(tmp_db, config)
            assert result is False

    def test_reflection_not_due_too_early(self, tmp_db, config):
        """Reflection is not due before the target hour."""
        from giva.intelligence.daily_review import is_reflection_due

        target_day = config.goals.weekly_reflection_day
        now = datetime.now()
        days_ahead = target_day - now.weekday()
        if days_ahead < 0:
            days_ahead += 7
        next_sunday = now + timedelta(days=days_ahead)
        fake_now = next_sunday.replace(hour=10, minute=0, second=0)  # morning

        with patch("giva.intelligence.daily_review.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = is_reflection_due(tmp_db, config)
            assert result is False


# --- Parse Agent Response ---


class TestParseAgentResponse:
    """Tests for the JSON parser used by post-chat agents."""

    def test_parse_clean_json(self):
        from giva.intelligence.agents import _parse_agent_response

        raw = '{"intents": [], "topic": "testing", "progress": []}'
        result = _parse_agent_response(raw)
        assert result is not None
        assert result["topic"] == "testing"

    def test_parse_json_with_think_tags(self):
        from giva.intelligence.agents import _parse_agent_response

        raw = '<think>reasoning here</think>{"intents": [], "topic": "work"}'
        result = _parse_agent_response(raw)
        assert result is not None
        assert result["topic"] == "work"

    def test_parse_json_in_markdown(self):
        from giva.intelligence.agents import _parse_agent_response

        raw = '```json\n{"intents": [], "topic": "email"}\n```'
        result = _parse_agent_response(raw)
        assert result is not None
        assert result["topic"] == "email"

    def test_parse_garbage_returns_none(self):
        from giva.intelligence.agents import _parse_agent_response

        result = _parse_agent_response("not json at all")
        assert result is None


# --- Fact Response Parser ---


class TestParseFactResponse:
    """Tests for _parse_fact_response."""

    def test_parse_clean_json(self):
        from giva.intelligence.daily_review import _parse_fact_response

        raw = '{"new_facts": ["fact1"], "obsolete_facts": []}'
        result = _parse_fact_response(raw)
        assert result is not None
        assert result["new_facts"] == ["fact1"]

    def test_parse_with_think_tags(self):
        from giva.intelligence.daily_review import _parse_fact_response

        raw = '<think>ok</think>{"new_facts": [], "obsolete_facts": []}'
        result = _parse_fact_response(raw)
        assert result is not None

    def test_parse_garbage_returns_none(self):
        from giva.intelligence.daily_review import _parse_fact_response

        result = _parse_fact_response("no json here")
        assert result is None
