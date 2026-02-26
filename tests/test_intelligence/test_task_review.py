"""Tests for the task review & classification pipeline.

Covers:
- JSON parsing (no LLM, no DB)
- Store integration (classification column, get_unclassified_tasks)
- Dedup pipeline (mocked LLM)
- Classification pipeline (mocked LLM)
- Full pipeline integration (mocked LLM)
- Action routing (project upgrade, enrichment)
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from giva.db.models import Email, Task


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

    def _mock_classify(self, tmp_db, config, classifications):
        """Helper: mock LLM + profile/goals and run classification."""
        from giva.intelligence.task_review import _classify_tasks

        tasks = tmp_db.get_unclassified_tasks()
        mock_response = json.dumps({"tasks": classifications})

        with patch("giva.llm.engine.manager") as mock_mgr, \
             patch("giva.intelligence.profile.get_profile_summary", return_value="Test user"), \
             patch("giva.intelligence.goals.get_goals_summary", return_value="No goals"):
            mock_mgr.generate.return_value = mock_response
            return _classify_tasks(tasks, tmp_db, config)

    def test_classify_autonomous(self, tmp_db, config):
        tid = tmp_db.add_task(Task(
            title="Search for leads in fintech", source_type="email", source_id=1,
        ))
        result = self._mock_classify(tmp_db, config, [{
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
        result = self._mock_classify(tmp_db, config, [{
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
        result = self._mock_classify(tmp_db, config, [{
            "task_id": tid,
            "classification": "project",
            "reasoning": "Multi-step initiative",
            "goal_title": "Redesign onboarding experience",
            "goal_tier": "mid_term",
        }])
        assert len(result) == 1
        assert result[0]["classification"] == "project"
        assert result[0]["goal_title"] == "Redesign onboarding experience"

    def test_classify_invalid_falls_back_to_needs_input(self, tmp_db, config):
        tid = tmp_db.add_task(Task(
            title="Do something", source_type="chat", source_id=0,
        ))
        result = self._mock_classify(tmp_db, config, [{
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
        result = self._mock_classify(tmp_db, config, [{
            "task_id": 9999,
            "classification": "autonomous",
            "reasoning": "nonexistent",
        }])
        assert len(result) == 0


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
