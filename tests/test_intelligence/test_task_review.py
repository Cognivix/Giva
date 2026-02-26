"""Tests for the task review & classification pipeline.

Covers:
- JSON parsing (no LLM, no DB)
- Store integration (classification column, get_unclassified_tasks)
- Sanity checks (expired deadlines, answered emails, past events)
- Dedup pipeline (mocked LLM)
- Classification pipeline (mocked LLM) — including dismiss
- Review memory and dismissal pattern learning
- Full pipeline integration (mocked LLM)
- Action routing (project upgrade, enrichment, dismiss)
- Source preservation (context_sources in post-chat agent)
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from giva.db.models import Email, Task, UserProfile


# ---------------------------------------------------------------------------
# JSON parsing tests (no LLM, no DB)
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    """Tests for the fail-safe JSON parser."""

    def test_parse_valid_json(self):
        from giva.intelligence.task_review import _parse_json_response

        raw = '{"groups": [{"canonical_id": 1, "duplicate_ids": [2]}]}'
        result = _parse_json_response(raw)
        assert result is not None
        assert len(result["groups"]) == 1

    def test_parse_with_think_tags(self):
        from giva.intelligence.task_review import _parse_json_response

        raw = '<think>reasoning here</think>\n{"tasks": []}'
        result = _parse_json_response(raw)
        assert result is not None
        assert result["tasks"] == []

    def test_parse_markdown_fenced(self):
        from giva.intelligence.task_review import _parse_json_response

        raw = 'Here is the result:\n```json\n{"tasks": [{"task_id": 1}]}\n```'
        result = _parse_json_response(raw)
        assert result is not None
        assert len(result["tasks"]) == 1

    def test_parse_embedded_json(self):
        from giva.intelligence.task_review import _parse_json_response

        raw = 'Some text before {"groups": []} and after'
        result = _parse_json_response(raw)
        assert result is not None
        assert result["groups"] == []

    def test_parse_garbage_returns_none(self):
        from giva.intelligence.task_review import _parse_json_response

        result = _parse_json_response("not json at all")
        assert result is None

    def test_parse_empty_returns_none(self):
        from giva.intelligence.task_review import _parse_json_response

        result = _parse_json_response("")
        assert result is None


# ---------------------------------------------------------------------------
# Store integration tests
# ---------------------------------------------------------------------------


class TestClassificationColumn:
    """Tests for the classification column in the tasks table."""

    def test_default_classification_is_null(self, tmp_db):
        """New tasks should have classification=None by default."""
        task_id = tmp_db.add_task(Task(
            title="Test task", source_type="email", source_id=1,
        ))
        task = tmp_db.get_task(task_id)
        assert task.classification is None

    def test_add_task_with_classification(self, tmp_db):
        """Tasks can be created with a classification."""
        task_id = tmp_db.add_task(Task(
            title="Research leads",
            source_type="email",
            source_id=1,
            classification="autonomous",
        ))
        task = tmp_db.get_task(task_id)
        assert task.classification == "autonomous"

    def test_update_task_classification(self, tmp_db):
        """Classification can be updated via update_task."""
        task_id = tmp_db.add_task(Task(
            title="Call John", source_type="chat", source_id=0,
        ))
        assert tmp_db.get_task(task_id).classification is None

        tmp_db.update_task(task_id, classification="user_only")
        assert tmp_db.get_task(task_id).classification == "user_only"

    def test_get_unclassified_tasks(self, tmp_db):
        """get_unclassified_tasks returns only pending/in_progress without classification."""
        # Unclassified pending
        id1 = tmp_db.add_task(Task(
            title="Unclassified 1", source_type="chat", source_id=0,
        ))
        # Unclassified in_progress
        id2 = tmp_db.add_task(Task(
            title="Unclassified 2", source_type="chat", source_id=0,
            status="in_progress",
        ))
        # Classified — should be excluded
        tmp_db.add_task(Task(
            title="Classified", source_type="chat", source_id=0,
            classification="needs_input",
        ))
        # Done — should be excluded
        tmp_db.add_task(Task(
            title="Done task", source_type="chat", source_id=0,
            status="done",
        ))

        unclassified = tmp_db.get_unclassified_tasks()
        ids = [t.id for t in unclassified]
        assert id1 in ids
        assert id2 in ids
        assert len(unclassified) == 2

    def test_get_unclassified_tasks_respects_limit(self, tmp_db):
        """Limit parameter is respected."""
        for i in range(5):
            tmp_db.add_task(Task(
                title=f"Task {i}", source_type="chat", source_id=0,
            ))
        result = tmp_db.get_unclassified_tasks(limit=3)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Sanity check tests (no LLM)
# ---------------------------------------------------------------------------


class TestSanityChecks:
    """Tests for pre-classification sanity checks."""

    def test_expired_deadline_dismissed(self, tmp_db):
        """Tasks with past due dates are auto-dismissed."""
        from giva.intelligence.task_review import _sanity_check_tasks

        past = datetime.now() - timedelta(days=2)
        task_id = tmp_db.add_task(Task(
            title="Expired task", source_type="chat", source_id=0,
            due_date=past,
        ))
        tasks = tmp_db.get_unclassified_tasks()
        surviving = _sanity_check_tasks(tasks, tmp_db)

        assert len(surviving) == 0
        dismissed = tmp_db.get_task(task_id)
        assert dismissed.status == "dismissed"
        assert dismissed.classification == "dismiss"

    def test_future_deadline_survives(self, tmp_db):
        """Tasks with future due dates are NOT dismissed."""
        from giva.intelligence.task_review import _sanity_check_tasks

        future = datetime.now() + timedelta(days=5)
        tmp_db.add_task(Task(
            title="Future task", source_type="chat", source_id=0,
            due_date=future,
        ))
        tasks = tmp_db.get_unclassified_tasks()
        surviving = _sanity_check_tasks(tasks, tmp_db)

        assert len(surviving) == 1

    def test_answered_email_dismissed(self, tmp_db):
        """Tasks about emails that have been replied to are dismissed."""
        from giva.intelligence.task_review import _sanity_check_tasks

        # Insert the original email
        email_id = tmp_db.upsert_email(Email(
            message_id="original@test.com",
            folder="INBOX",
            from_addr="sender@test.com",
            subject="Original question",
            date_sent=datetime(2026, 2, 20),
        ))
        # Insert a reply to that email
        tmp_db.upsert_email(Email(
            message_id="reply@test.com",
            folder="Sent",
            from_addr="me@test.com",
            subject="Re: Original question",
            date_sent=datetime(2026, 2, 21),
            in_reply_to="original@test.com",
        ))
        # Task linked to the original email
        task_id = tmp_db.add_task(Task(
            title="Reply to sender about question",
            source_type="email",
            source_id=email_id,
        ))

        tasks = tmp_db.get_unclassified_tasks()
        surviving = _sanity_check_tasks(tasks, tmp_db)

        assert len(surviving) == 0
        assert tmp_db.get_task(task_id).status == "dismissed"

    def test_unanswered_email_survives(self, tmp_db):
        """Tasks about unreplied emails are NOT dismissed."""
        from giva.intelligence.task_review import _sanity_check_tasks

        email_id = tmp_db.upsert_email(Email(
            message_id="unanswered@test.com",
            folder="INBOX",
            from_addr="sender@test.com",
            subject="Waiting for reply",
            date_sent=datetime(2026, 2, 20),
        ))
        tmp_db.add_task(Task(
            title="Reply to sender", source_type="email", source_id=email_id,
        ))
        tasks = tmp_db.get_unclassified_tasks()
        surviving = _sanity_check_tasks(tasks, tmp_db)

        assert len(surviving) == 1

    def test_past_event_dismissed(self, tmp_db):
        """Tasks about past events are dismissed."""
        from giva.intelligence.task_review import _sanity_check_tasks

        past = datetime.now() - timedelta(hours=3)
        # Insert the event
        with tmp_db._conn() as conn:
            conn.execute(
                "INSERT INTO events (uid, calendar_name, summary, dtstart) "
                "VALUES (?, ?, ?, ?)",
                ("past-event", "Work", "Team standup", past.isoformat()),
            )
            event_id = conn.execute(
                "SELECT id FROM events WHERE uid = 'past-event'"
            ).fetchone()["id"]

        task_id = tmp_db.add_task(Task(
            title="Prepare for standup", source_type="event", source_id=event_id,
        ))
        tasks = tmp_db.get_unclassified_tasks()
        surviving = _sanity_check_tasks(tasks, tmp_db)

        assert len(surviving) == 0
        assert tmp_db.get_task(task_id).status == "dismissed"

    def test_future_event_survives(self, tmp_db):
        """Tasks about future events are NOT dismissed."""
        from giva.intelligence.task_review import _sanity_check_tasks

        future = datetime.now() + timedelta(hours=5)
        with tmp_db._conn() as conn:
            conn.execute(
                "INSERT INTO events (uid, calendar_name, summary, dtstart) "
                "VALUES (?, ?, ?, ?)",
                ("future-event", "Work", "Planning", future.isoformat()),
            )
            event_id = conn.execute(
                "SELECT id FROM events WHERE uid = 'future-event'"
            ).fetchone()["id"]

        tmp_db.add_task(Task(
            title="Prepare for planning", source_type="event", source_id=event_id,
        ))
        tasks = tmp_db.get_unclassified_tasks()
        surviving = _sanity_check_tasks(tasks, tmp_db)

        assert len(surviving) == 1

    def test_chat_tasks_always_survive(self, tmp_db):
        """Tasks with source_type='chat' are never sanity-dismissed."""
        from giva.intelligence.task_review import _sanity_check_tasks

        tmp_db.add_task(Task(
            title="Chat task", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_unclassified_tasks()
        surviving = _sanity_check_tasks(tasks, tmp_db)

        assert len(surviving) == 1

    def test_sanity_broadcasts(self, tmp_db):
        """Sanity dismissals emit SSE broadcasts."""
        from giva.intelligence.task_review import _sanity_check_tasks

        past = datetime.now() - timedelta(days=1)
        tmp_db.add_task(Task(
            title="Expired", source_type="chat", source_id=0,
            due_date=past,
        ))
        tasks = tmp_db.get_unclassified_tasks()
        broadcast = MagicMock()
        _sanity_check_tasks(tasks, tmp_db, broadcast_fn=broadcast)

        assert broadcast.called
        event = broadcast.call_args[0][0]
        assert event["event"] == "task_sanity_dismissed"


# ---------------------------------------------------------------------------
# Dedup pipeline tests (mocked LLM)
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    """Tests for _detect_duplicates and _execute_merges."""

    def test_detect_duplicates_groups_similar_tasks(self, tmp_db, config):
        from giva.intelligence.task_review import _detect_duplicates

        id1 = tmp_db.add_task(Task(
            title="Reply to Sarah's email", source_type="email", source_id=1,
        ))
        id2 = tmp_db.add_task(Task(
            title="Respond to Sarah about budget", source_type="email", source_id=2,
        ))
        tasks = tmp_db.get_unclassified_tasks()

        mock_response = json.dumps({
            "groups": [{
                "canonical_id": id1,
                "duplicate_ids": [id2],
                "merged_title": "Reply to Sarah about Q4 budget",
                "merged_description": None,
            }],
        })

        with patch("giva.llm.engine.manager") as mock_mgr:
            mock_mgr.generate.return_value = mock_response
            groups = _detect_duplicates(tasks, config)

        assert len(groups) == 1
        assert groups[0]["canonical_id"] == id1
        assert groups[0]["duplicate_ids"] == [id2]

    def test_detect_duplicates_no_duplicates(self, tmp_db, config):
        from giva.intelligence.task_review import _detect_duplicates

        tmp_db.add_task(Task(
            title="Buy groceries", source_type="chat", source_id=0,
        ))
        tmp_db.add_task(Task(
            title="Write report", source_type="email", source_id=1,
        ))
        tasks = tmp_db.get_unclassified_tasks()

        mock_response = json.dumps({"groups": []})

        with patch("giva.llm.engine.manager") as mock_mgr:
            mock_mgr.generate.return_value = mock_response
            groups = _detect_duplicates(tasks, config)

        assert groups == []

    def test_detect_duplicates_single_task(self, tmp_db, config):
        """Single task should skip dedup entirely."""
        from giva.intelligence.task_review import _detect_duplicates

        tmp_db.add_task(Task(
            title="Only task", source_type="chat", source_id=0,
        ))
        tasks = tmp_db.get_unclassified_tasks()

        result = _detect_duplicates(tasks, config)
        assert result == []

    def test_execute_merges_dismisses_duplicates(self, tmp_db):
        from giva.intelligence.task_review import _execute_merges

        id1 = tmp_db.add_task(Task(
            title="Original", source_type="chat", source_id=0, priority="low",
        ))
        id2 = tmp_db.add_task(Task(
            title="Duplicate", source_type="email", source_id=1, priority="high",
        ))

        groups = [{
            "canonical_id": id1,
            "duplicate_ids": [id2],
            "merged_title": "Improved title",
            "merged_description": None,
        }]

        dismissed = _execute_merges(groups, tmp_db)
        assert dismissed == 1

        # Canonical was updated
        canonical = tmp_db.get_task(id1)
        assert canonical.title == "Improved title"
        assert canonical.priority == "high"  # Took higher priority

        # Duplicate was dismissed
        dup = tmp_db.get_task(id2)
        assert dup.status == "dismissed"

    def test_execute_merges_takes_earliest_due_date(self, tmp_db):
        from giva.intelligence.task_review import _execute_merges

        early = datetime(2026, 3, 1)
        late = datetime(2026, 4, 1)

        id1 = tmp_db.add_task(Task(
            title="Task A", source_type="chat", source_id=0, due_date=late,
        ))
        id2 = tmp_db.add_task(Task(
            title="Task B", source_type="chat", source_id=0, due_date=early,
        ))

        groups = [{
            "canonical_id": id1,
            "duplicate_ids": [id2],
            "merged_title": None,
            "merged_description": None,
        }]

        _execute_merges(groups, tmp_db)
        canonical = tmp_db.get_task(id1)
        assert canonical.due_date == early

    def test_execute_merges_broadcasts(self, tmp_db):
        from giva.intelligence.task_review import _execute_merges

        id1 = tmp_db.add_task(Task(
            title="Keep", source_type="chat", source_id=0,
        ))
        id2 = tmp_db.add_task(Task(
            title="Remove", source_type="chat", source_id=0,
        ))

        broadcast = MagicMock()
        groups = [{
            "canonical_id": id1,
            "duplicate_ids": [id2],
            "merged_title": None,
            "merged_description": None,
        }]

        _execute_merges(groups, tmp_db, broadcast_fn=broadcast)
        assert broadcast.called
        event = broadcast.call_args[0][0]
        assert event["event"] == "tasks_merged"


# ---------------------------------------------------------------------------
# Classification pipeline tests (mocked LLM)
# ---------------------------------------------------------------------------


class TestClassification:
    """Tests for _classify_tasks."""

    def _mock_classify(self, tmp_db, config, classifications, observations=None):
        """Helper: mock LLM + profile/goals and run classification."""
        from giva.intelligence.task_review import _classify_tasks

        tasks = tmp_db.get_unclassified_tasks()
        response = {"tasks": classifications}
        if observations:
            response["review_observations"] = observations
        mock_response = json.dumps(response)

        with patch("giva.llm.engine.manager") as mock_mgr, \
             patch("giva.intelligence.profile.get_profile_summary", return_value="Test user"), \
             patch("giva.intelligence.goals.get_goals_summary", return_value="No goals"):
            mock_mgr.generate.return_value = mock_response
            return _classify_tasks(tasks, tmp_db, config)

    def test_classify_autonomous(self, tmp_db, config):
        tid = tmp_db.add_task(Task(
            title="Search for leads in fintech", source_type="email", source_id=1,
        ))
        result, _ = self._mock_classify(tmp_db, config, [{
            "task_id": tid,
            "classification": "autonomous",
            "reasoning": "Simple research task",
            "suggested_agent": "orchestrator",
        }])
        assert len(result) == 1
        assert result[0]["classification"] == "autonomous"
        assert result[0]["suggested_agent"] == "orchestrator"

    def test_classify_needs_input(self, tmp_db, config):
        tid = tmp_db.add_task(Task(
            title="Draft response to vendor proposal", source_type="email", source_id=1,
        ))
        result, _ = self._mock_classify(tmp_db, config, [{
            "task_id": tid,
            "classification": "needs_input",
            "reasoning": "User needs to decide direction",
            "enrichment_query": "vendor proposal",
        }])
        assert len(result) == 1
        assert result[0]["classification"] == "needs_input"
        assert result[0]["enrichment_query"] == "vendor proposal"

    def test_classify_project(self, tmp_db, config):
        tid = tmp_db.add_task(Task(
            title="Redesign the onboarding flow", source_type="chat", source_id=0,
        ))
        result, _ = self._mock_classify(tmp_db, config, [{
            "task_id": tid,
            "classification": "project",
            "reasoning": "Multi-step initiative",
            "goal_title": "Redesign onboarding experience",
            "goal_tier": "mid_term",
        }])
        assert len(result) == 1
        assert result[0]["classification"] == "project"
        assert result[0]["goal_title"] == "Redesign onboarding experience"

    def test_classify_dismiss(self, tmp_db, config):
        """LLM can classify a task as 'dismiss'."""
        tid = tmp_db.add_task(Task(
            title="Prepare for internal standup", source_type="event", source_id=1,
        ))
        result, _ = self._mock_classify(tmp_db, config, [{
            "task_id": tid,
            "classification": "dismiss",
            "reasoning": "Trivial internal meeting, calendar is enough",
        }])
        assert len(result) == 1
        assert result[0]["classification"] == "dismiss"

    def test_classify_returns_observations(self, tmp_db, config):
        """LLM review_observations are returned alongside classifications."""
        tid = tmp_db.add_task(Task(
            title="Some task", source_type="chat", source_id=0,
        ))
        _, observations = self._mock_classify(
            tmp_db, config,
            [{"task_id": tid, "classification": "needs_input", "reasoning": "test"}],
            observations="User tends to dismiss meeting prep tasks",
        )
        assert observations == "User tends to dismiss meeting prep tasks"

    def test_classify_invalid_falls_back_to_needs_input(self, tmp_db, config):
        tid = tmp_db.add_task(Task(
            title="Do something", source_type="chat", source_id=0,
        ))
        result, _ = self._mock_classify(tmp_db, config, [{
            "task_id": tid,
            "classification": "invalid_category",
            "reasoning": "test",
        }])
        assert len(result) == 1
        assert result[0]["classification"] == "needs_input"

    def test_classify_filters_invalid_task_ids(self, tmp_db, config):
        tmp_db.add_task(Task(
            title="Real task", source_type="chat", source_id=0,
        ))
        result, _ = self._mock_classify(tmp_db, config, [{
            "task_id": 9999,
            "classification": "autonomous",
            "reasoning": "nonexistent",
        }])
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Review memory and dismissal pattern learning
# ---------------------------------------------------------------------------


class TestReviewMemory:
    """Tests for dismissal pattern learning and review memory persistence."""

    def test_learn_dismissal_patterns_updates_profile(self, tmp_db):
        """Pattern learning stores suppressed types in profile_data."""
        from giva.intelligence.task_review import _learn_dismissal_patterns

        # Create a user profile
        tmp_db.upsert_profile(UserProfile(display_name="Test", email_address="t@t.com"))

        # Add many dismissed event tasks to trigger pattern detection
        for i in range(6):
            tid = tmp_db.add_task(Task(
                title=f"Prepare for meeting {i}",
                source_type="event",
                source_id=i + 1,
                classification="dismiss",
            ))
            tmp_db.update_task_status(tid, "dismissed")

        _learn_dismissal_patterns(tmp_db)

        profile = tmp_db.get_profile()
        patterns = profile.profile_data.get("task_review_patterns", {})
        assert "suppressed_types" in patterns
        assert len(patterns["suppressed_types"]) > 0
        # Should detect event tasks as a pattern
        assert any("event" in s for s in patterns["suppressed_types"])

    def test_learn_dismissal_patterns_needs_minimum(self, tmp_db):
        """Pattern learning does nothing with fewer than 3 dismissed tasks."""
        from giva.intelligence.task_review import _learn_dismissal_patterns

        tmp_db.upsert_profile(UserProfile(display_name="Test", email_address="t@t.com"))
        tid = tmp_db.add_task(Task(
            title="One task", source_type="chat", source_id=0,
        ))
        tmp_db.update_task_status(tid, "dismissed")

        _learn_dismissal_patterns(tmp_db)

        profile = tmp_db.get_profile()
        patterns = profile.profile_data.get("task_review_patterns", {})
        assert patterns == {}

    def test_save_review_observations(self, tmp_db):
        """LLM observations are persisted to profile_data."""
        from giva.intelligence.task_review import _save_review_observations

        tmp_db.upsert_profile(UserProfile(display_name="Test", email_address="t@t.com"))

        _save_review_observations("User ignores standup prep tasks", tmp_db)

        profile = tmp_db.get_profile()
        patterns = profile.profile_data.get("task_review_patterns", {})
        assert "User ignores standup prep tasks" in patterns.get("observations", [])

    def test_save_observations_deduplicates(self, tmp_db):
        """Duplicate observations are not added twice."""
        from giva.intelligence.task_review import _save_review_observations

        tmp_db.upsert_profile(UserProfile(display_name="Test", email_address="t@t.com"))

        _save_review_observations("Pattern A", tmp_db)
        _save_review_observations("Pattern A", tmp_db)
        _save_review_observations("Pattern B", tmp_db)

        profile = tmp_db.get_profile()
        obs = profile.profile_data["task_review_patterns"]["observations"]
        assert obs.count("Pattern A") == 1
        assert "Pattern B" in obs

    def test_get_review_memory(self, tmp_db):
        """Review memory is formatted correctly for the classify prompt."""
        from giva.intelligence.task_review import _get_review_memory

        tmp_db.upsert_profile(UserProfile(
            display_name="Test",
            email_address="t@t.com",
            profile_data={
                "task_review_patterns": {
                    "observations": ["User prefers email over meetings"],
                    "suppressed_types": ["event tasks (dismissed 8 times)"],
                }
            },
        ))

        memory = _get_review_memory(tmp_db)
        assert "Review memory" in memory
        assert "User prefers email over meetings" in memory
        assert "event tasks (dismissed 8 times)" in memory

    def test_get_review_memory_empty(self, tmp_db):
        """Empty review memory returns empty string."""
        from giva.intelligence.task_review import _get_review_memory

        tmp_db.upsert_profile(UserProfile(display_name="Test", email_address="t@t.com"))
        assert _get_review_memory(tmp_db) == ""

    def test_get_dismissal_history(self, tmp_db):
        """Dismissal history lists recently dismissed tasks."""
        from giva.intelligence.task_review import _get_dismissal_history

        for i in range(3):
            tid = tmp_db.add_task(Task(
                title=f"Dismissed task {i}", source_type="event", source_id=i,
            ))
            tmp_db.update_task_status(tid, "dismissed")

        history = _get_dismissal_history(tmp_db)
        assert "Recently dismissed" in history
        assert "Dismissed task 0" in history


# ---------------------------------------------------------------------------
# Action routing tests
# ---------------------------------------------------------------------------


class TestRouteProject:
    """Tests for _route_project — upgrading tasks to goals."""

    def test_upgrade_creates_goal_and_dismisses_task(self, tmp_db):
        from giva.intelligence.task_review import _route_project

        task_id = tmp_db.add_task(Task(
            title="Build analytics dashboard",
            source_type="chat",
            source_id=0,
            description="Complex multi-week project",
            priority="high",
        ))
        task = tmp_db.get_task(task_id)

        cls = {
            "classification": "project",
            "goal_title": "Analytics Dashboard",
            "goal_tier": "mid_term",
        }

        action = _route_project(task, cls, tmp_db)
        assert action is not None
        assert action["type"] == "task_upgraded_to_goal"
        assert action["goal_title"] == "Analytics Dashboard"

        # Task was dismissed
        updated_task = tmp_db.get_task(task_id)
        assert updated_task.status == "dismissed"

        # Goal was created
        goal = tmp_db.get_goal(action["goal_id"])
        assert goal is not None
        assert goal.title == "Analytics Dashboard"
        assert goal.tier == "mid_term"
        assert goal.priority == "high"

    def test_upgrade_broadcasts(self, tmp_db):
        from giva.intelligence.task_review import _route_project

        task_id = tmp_db.add_task(Task(
            title="Big project", source_type="chat", source_id=0,
        ))
        task = tmp_db.get_task(task_id)
        cls = {"classification": "project", "goal_title": "Big project", "goal_tier": "mid_term"}

        broadcast = MagicMock()
        _route_project(task, cls, tmp_db, broadcast_fn=broadcast)
        assert broadcast.called
        event = broadcast.call_args[0][0]
        assert event["event"] == "task_upgraded_to_goal"


class TestRouteDismiss:
    """Tests for _route_dismiss — dismissing unnecessary tasks."""

    def test_dismiss_updates_status(self, tmp_db):
        from giva.intelligence.task_review import _route_dismiss

        task_id = tmp_db.add_task(Task(
            title="Trivial meeting prep", source_type="event", source_id=1,
        ))
        task = tmp_db.get_task(task_id)
        cls = {"classification": "dismiss", "reasoning": "Internal meeting, calendar suffices"}

        action = _route_dismiss(task, cls, tmp_db)

        assert action is not None
        assert action["type"] == "task_dismissed"
        assert action["task_id"] == task_id

        updated = tmp_db.get_task(task_id)
        assert updated.status == "dismissed"

    def test_dismiss_broadcasts(self, tmp_db):
        from giva.intelligence.task_review import _route_dismiss

        task_id = tmp_db.add_task(Task(
            title="Not needed", source_type="chat", source_id=0,
        ))
        task = tmp_db.get_task(task_id)
        cls = {"classification": "dismiss", "reasoning": "User never acts on these"}

        broadcast = MagicMock()
        _route_dismiss(task, cls, tmp_db, broadcast_fn=broadcast)

        assert broadcast.called
        event = broadcast.call_args[0][0]
        assert event["event"] == "task_dismissed"
        data = json.loads(event["data"])
        assert data["reasoning"] == "User never acts on these"


class TestRouteEnrich:
    """Tests for _route_enrich — context enrichment."""

    def test_enrich_updates_description(self, tmp_db, config):
        from giva.intelligence.task_review import _route_enrich

        # Add an email that matches the enrichment query so FTS returns context
        tmp_db.upsert_email(Email(
            message_id="test-email-1",
            folder="INBOX",
            from_addr="client@example.com",
            from_name="Client",
            subject="Follow up on Q4 review",
            date_sent=datetime(2026, 2, 20),
            body_plain="Hi, just wanted to follow up on the Q4 review discussion.",
        ))

        task_id = tmp_db.add_task(Task(
            title="Follow up with client",
            source_type="email",
            source_id=1,
            description="Original description",
        ))
        task = tmp_db.get_task(task_id)
        cls = {
            "classification": "needs_input",
            "enrichment_query": "client follow up",
        }

        mock_response = json.dumps({
            "enriched_description": "Enriched: Follow up with client about Q4 review."
        })

        with patch("giva.llm.engine.manager") as mock_mgr:
            mock_mgr.generate.return_value = mock_response
            action = _route_enrich(task, cls, tmp_db, config)

        assert action is not None
        assert action["type"] == "task_enriched"

        updated = tmp_db.get_task(task_id)
        assert "Enriched" in updated.description


# ---------------------------------------------------------------------------
# Source preservation tests
# ---------------------------------------------------------------------------


class TestSourcePreservation:
    """Tests for _pick_best_source and context_sources in post-chat agent."""

    def test_pick_best_source_email_first(self):
        from giva.intelligence.agents import _pick_best_source

        sources = {"email_ids": [42, 43], "event_ids": [10]}
        src_type, src_id = _pick_best_source(sources)
        assert src_type == "email"
        assert src_id == 42

    def test_pick_best_source_event_fallback(self):
        from giva.intelligence.agents import _pick_best_source

        sources = {"email_ids": [], "event_ids": [10, 11]}
        src_type, src_id = _pick_best_source(sources)
        assert src_type == "event"
        assert src_id == 10

    def test_pick_best_source_chat_default(self):
        from giva.intelligence.agents import _pick_best_source

        src_type, src_id = _pick_best_source(None)
        assert src_type == "chat"
        assert src_id == 0

    def test_pick_best_source_empty(self):
        from giva.intelligence.agents import _pick_best_source

        sources = {"email_ids": [], "event_ids": []}
        src_type, src_id = _pick_best_source(sources)
        assert src_type == "chat"
        assert src_id == 0

    def test_create_task_uses_context_sources(self, tmp_db):
        """_handle_create_task uses email source from context_sources."""
        from giva.intelligence.agents import _handle_create_task

        intent = {"title": "Follow up on email", "description": "Test"}
        context_sources = {"email_ids": [42], "event_ids": []}

        action = _handle_create_task(intent, tmp_db, context_sources=context_sources)
        assert action is not None

        task = tmp_db.get_task(action["task_id"])
        assert task.source_type == "email"
        assert task.source_id == 42

    def test_create_task_falls_back_to_chat(self, tmp_db):
        """Without context_sources, tasks use chat/0."""
        from giva.intelligence.agents import _handle_create_task

        intent = {"title": "Some task from chat", "description": ""}
        action = _handle_create_task(intent, tmp_db)
        assert action is not None

        task = tmp_db.get_task(action["task_id"])
        assert task.source_type == "chat"
        assert task.source_id == 0


class TestRetrieveContextSources:
    """Tests for retrieve_context_sources in context.py."""

    def test_returns_email_ids(self, tmp_db):
        from giva.intelligence.context import retrieve_context_sources

        tmp_db.upsert_email(Email(
            message_id="ctx-email-1",
            folder="INBOX",
            from_addr="bob@test.com",
            subject="Project update discussion",
            date_sent=datetime(2026, 2, 20),
            body_plain="Here is the project update.",
        ))

        sources = retrieve_context_sources("project update", tmp_db)
        assert len(sources["email_ids"]) >= 1

    def test_returns_empty_on_no_match(self, tmp_db):
        from giva.intelligence.context import retrieve_context_sources

        sources = retrieve_context_sources("zzz nonexistent query 123", tmp_db)
        # May still have recent emails/events, but shouldn't crash
        assert "email_ids" in sources
        assert "event_ids" in sources


# ---------------------------------------------------------------------------
# Full pipeline integration (mocked LLM)
# ---------------------------------------------------------------------------


class TestReviewPendingTasks:
    """Integration tests for the full review_pending_tasks pipeline."""

    def test_returns_zero_when_no_tasks(self, tmp_db, config):
        from giva.intelligence.task_review import review_pending_tasks

        result = review_pending_tasks(tmp_db, config)
        assert result == 0

    def test_returns_zero_when_disabled(self, tmp_db, config):
        from giva.intelligence.task_review import review_pending_tasks
        from giva.config import GivaConfig, TaskReviewConfig

        disabled_config = GivaConfig(
            data_dir=config.data_dir,
            task_review=TaskReviewConfig(enabled=False),
        )
        tmp_db.add_task(Task(
            title="Some task", source_type="chat", source_id=0,
        ))
        result = review_pending_tasks(tmp_db, disabled_config)
        assert result == 0

    def test_full_pipeline_classifies_tasks(self, tmp_db, config):
        from giva.intelligence.task_review import review_pending_tasks

        id1 = tmp_db.add_task(Task(
            title="Search competitors", source_type="email", source_id=1,
        ))
        id2 = tmp_db.add_task(Task(
            title="Call investor", source_type="chat", source_id=0,
        ))

        # Dedup returns no duplicates; classify returns results
        dedup_response = json.dumps({"groups": []})
        classify_response = json.dumps({"tasks": [
            {"task_id": id1, "classification": "autonomous",
             "reasoning": "research", "suggested_agent": "orchestrator"},
            {"task_id": id2, "classification": "user_only",
             "reasoning": "personal call", "enrichment_query": "investor"},
        ]})

        call_count = [0]

        def mock_generate(model, messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return dedup_response
            return classify_response

        with patch("giva.llm.engine.manager") as mock_mgr, \
             patch("giva.intelligence.profile.get_profile_summary", return_value=""), \
             patch("giva.intelligence.goals.get_goals_summary", return_value=""):
            mock_mgr.generate.side_effect = mock_generate
            count = review_pending_tasks(tmp_db, config)

        assert count == 2

        t1 = tmp_db.get_task(id1)
        assert t1.classification == "autonomous"

        t2 = tmp_db.get_task(id2)
        assert t2.classification == "user_only"

    def test_pipeline_skips_already_classified(self, tmp_db, config):
        from giva.intelligence.task_review import review_pending_tasks

        # Already classified — should be skipped
        tmp_db.add_task(Task(
            title="Already done",
            source_type="chat",
            source_id=0,
            classification="needs_input",
        ))

        result = review_pending_tasks(tmp_db, config)
        assert result == 0

    def test_pipeline_sanity_checks_run_first(self, tmp_db, config):
        """Expired tasks are dismissed before LLM classification."""
        from giva.intelligence.task_review import review_pending_tasks

        past = datetime.now() - timedelta(days=3)
        tmp_db.add_task(Task(
            title="Expired task", source_type="chat", source_id=0,
            due_date=past,
        ))

        # No LLM calls should happen — sanity check dismisses everything
        result = review_pending_tasks(tmp_db, config)
        assert result == 0

    def test_pipeline_dismiss_classification(self, tmp_db, config):
        """Tasks classified as 'dismiss' are properly dismissed in the pipeline."""
        from giva.intelligence.task_review import review_pending_tasks

        tid = tmp_db.add_task(Task(
            title="Trivial meeting prep", source_type="event", source_id=1,
        ))
        # Need a second task so dedup doesn't get skipped
        tid2 = tmp_db.add_task(Task(
            title="Another task", source_type="chat", source_id=0,
        ))

        dedup_response = json.dumps({"groups": []})
        classify_response = json.dumps({"tasks": [
            {"task_id": tid, "classification": "dismiss",
             "reasoning": "Trivial internal meeting"},
            {"task_id": tid2, "classification": "needs_input",
             "reasoning": "Needs user input"},
        ]})

        call_count = [0]

        def mock_generate(model, messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return dedup_response
            return classify_response

        with patch("giva.llm.engine.manager") as mock_mgr, \
             patch("giva.intelligence.profile.get_profile_summary", return_value=""), \
             patch("giva.intelligence.goals.get_goals_summary", return_value=""):
            mock_mgr.generate.side_effect = mock_generate
            count = review_pending_tasks(tmp_db, config)

        assert count == 2
        assert tmp_db.get_task(tid).status == "dismissed"
        assert tmp_db.get_task(tid).classification == "dismiss"
        assert tmp_db.get_task(tid2).classification == "needs_input"
