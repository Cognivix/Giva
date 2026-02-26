"""Tests for the WebOrchestratorAgent class."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from giva.agents.web_orchestrator.agent import WebOrchestratorAgent, _validate_plan
from giva.config import GivaConfig
from giva.db.models import Goal
from giva.db.store import Store
from giva.llm.structured import WebPlan, WebPlanSubtask


# ---------------------------------------------------------------------------
# Manifest tests
# ---------------------------------------------------------------------------


class TestManifest:
    def test_agent_id(self):
        agent = WebOrchestratorAgent()
        assert agent.manifest.agent_id == "web_orchestrator"

    def test_model_tier_assistant(self):
        agent = WebOrchestratorAgent()
        assert agent.manifest.model_tier == "assistant"

    def test_requires_confirmation(self):
        agent = WebOrchestratorAgent()
        assert agent.manifest.requires_confirmation is True

    def test_has_examples(self):
        agent = WebOrchestratorAgent()
        assert len(agent.manifest.examples) >= 3


# ---------------------------------------------------------------------------
# Plan validation tests
# ---------------------------------------------------------------------------


class TestValidatePlan:
    def test_valid_plan(self):
        plan = WebPlan(
            goal="test",
            subtasks=[
                WebPlanSubtask(
                    objective="Click login",
                    target_url="https://example.com/login",
                ),
            ],
        )
        valid, msg = _validate_plan(plan)
        assert valid is True
        assert msg == ""

    def test_empty_subtasks(self):
        plan = WebPlan(goal="test", subtasks=[])
        valid, msg = _validate_plan(plan)
        assert valid is False
        assert "no subtasks" in msg.lower()

    def test_too_many_subtasks(self):
        plan = WebPlan(
            goal="test",
            subtasks=[
                WebPlanSubtask(
                    objective=f"Step {i}",
                    target_url=f"https://example.com/{i}",
                )
                for i in range(10)
            ],
        )
        valid, msg = _validate_plan(plan)
        assert valid is False
        assert "too many" in msg.lower()

    def test_invalid_url(self):
        plan = WebPlan(
            goal="test",
            subtasks=[
                WebPlanSubtask(objective="Click login", target_url="not-a-url"),
            ],
        )
        valid, msg = _validate_plan(plan)
        assert valid is False
        assert "invalid url" in msg.lower()

    def test_empty_objective(self):
        plan = WebPlan(
            goal="test",
            subtasks=[
                WebPlanSubtask(objective="  ", target_url="https://example.com"),
            ],
        )
        valid, msg = _validate_plan(plan)
        assert valid is False
        assert "empty objective" in msg.lower()


# ---------------------------------------------------------------------------
# Execute tests
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.fixture
    def agent(self):
        return WebOrchestratorAgent()

    @pytest.fixture
    def store(self, tmp_path):
        return Store(tmp_path / "test.db")

    @pytest.fixture
    def config(self, tmp_path):
        return GivaConfig(data_dir=tmp_path)

    @pytest.fixture
    def goal_id(self, store):
        return store.add_goal(Goal(title="Test Goal", tier="short_term"))

    def test_no_goal_id_fails(self, agent, store, config):
        result = agent.execute("Test query", {}, store, config)
        assert result.success is False
        assert "goal" in result.output.lower()

    @patch.object(WebOrchestratorAgent, "_generate_plan", return_value=None)
    def test_plan_generation_failure(self, mock_plan, agent, store, config, goal_id):
        result = agent.execute(
            "Test query", {"goal_id": goal_id, "job_id": "j1"}, store, config
        )
        assert result.success is False
        assert "couldn't break down" in result.output.lower()

    @patch.object(WebOrchestratorAgent, "_generate_plan")
    def test_successful_plan(self, mock_plan, agent, store, config, goal_id):
        mock_plan.return_value = WebPlan(
            goal="Login to example.com",
            subtasks=[
                WebPlanSubtask(
                    objective="Navigate and click login",
                    target_url="https://example.com/login",
                    expected_outcome="Login page visible",
                ),
                WebPlanSubtask(
                    objective="Enter credentials",
                    target_url="https://example.com/login",
                    expected_outcome="Dashboard visible",
                ),
            ],
        )

        result = agent.execute(
            "Login to example.com",
            {"goal_id": goal_id, "job_id": "job-123"},
            store,
            config,
        )

        assert result.success is True
        assert "2 steps" in result.output
        assert result.actions[0]["type"] == "vlm_plan_created"
        assert result.actions[0]["subtask_count"] == 2

        # Verify tasks written to DB
        tasks = store.get_vlm_tasks(job_id="job-123")
        assert len(tasks) == 2
        assert tasks[0].sequence == 0
        assert tasks[1].sequence == 1
        assert tasks[0].goal_id == goal_id

    @patch.object(WebOrchestratorAgent, "_generate_plan")
    def test_invalid_plan_rejected(self, mock_plan, agent, store, config, goal_id):
        mock_plan.return_value = WebPlan(
            goal="test",
            subtasks=[
                WebPlanSubtask(objective="Click", target_url="not-valid"),
            ],
        )

        result = agent.execute(
            "test", {"goal_id": goal_id, "job_id": "j1"}, store, config
        )
        assert result.success is False
        assert "issue" in result.output.lower()


# ---------------------------------------------------------------------------
# AGENT_CLASS discovery
# ---------------------------------------------------------------------------


def test_agent_class_exported():
    from giva.agents.web_orchestrator.agent import AGENT_CLASS

    assert AGENT_CLASS is WebOrchestratorAgent
