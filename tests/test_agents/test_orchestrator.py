"""Tests for the OrchestratorAgent class."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from giva.agents.orchestrator.agent import OrchestratorAgent
from giva.agents.orchestrator.executor import SubTaskResult
from giva.config import GivaConfig
from giva.llm.structured import OrchestratorPlan, SubTask

_AGENT_PLAN = "giva.agents.orchestrator.agent.generate_plan"
_AGENT_VALIDATE = "giva.agents.orchestrator.agent.validate_plan"
_AGENT_EXECUTE = "giva.agents.orchestrator.agent.execute_plan"


# ---------------------------------------------------------------------------
# Manifest tests
# ---------------------------------------------------------------------------


class TestManifest:
    def test_agent_id(self):
        agent = OrchestratorAgent()
        assert agent.manifest.agent_id == "orchestrator"

    def test_model_tier_assistant(self):
        agent = OrchestratorAgent()
        assert agent.manifest.model_tier == "assistant"

    def test_requires_confirmation(self):
        agent = OrchestratorAgent()
        assert agent.manifest.requires_confirmation is True

    def test_has_examples(self):
        agent = OrchestratorAgent()
        assert len(agent.manifest.examples) >= 3


# ---------------------------------------------------------------------------
# plan_only tests
# ---------------------------------------------------------------------------


class TestPlanOnly:
    @patch(_AGENT_VALIDATE, return_value=(True, ""))
    @patch(_AGENT_PLAN)
    def test_returns_valid_plan(self, mock_plan, _mock_validate):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[SubTask(id=1, description="do", agent_id="x", query="q")],
        )
        mock_plan.return_value = plan

        agent = OrchestratorAgent()
        config = GivaConfig()
        result = agent.plan_only("do something", config)

        assert result is not None
        assert result.goal == "test"

    @patch(_AGENT_PLAN, return_value=None)
    def test_returns_none_on_failure(self, _mock_plan):
        agent = OrchestratorAgent()
        config = GivaConfig()
        result = agent.plan_only("do something", config)
        assert result is None

    @patch(_AGENT_VALIDATE, return_value=(False, "bad plan"))
    @patch(_AGENT_PLAN)
    def test_returns_none_on_invalid_plan(self, mock_plan, _mock_validate):
        plan = OrchestratorPlan(goal="test", subtasks=[])
        mock_plan.return_value = plan

        agent = OrchestratorAgent()
        config = GivaConfig()
        result = agent.plan_only("do something", config)
        assert result is None


# ---------------------------------------------------------------------------
# execute tests
# ---------------------------------------------------------------------------


class TestExecute:
    @patch("giva.agents.orchestrator.agent.OrchestratorAgent._llm_generate")
    @patch(_AGENT_EXECUTE)
    @patch(_AGENT_VALIDATE, return_value=(True, ""))
    @patch(_AGENT_PLAN)
    def test_end_to_end_success(
        self, mock_plan, _mock_validate, mock_execute, mock_llm,
    ):
        plan = OrchestratorPlan(
            goal="Research and email",
            reasoning="Two steps",
            subtasks=[
                SubTask(
                    id=1, description="Research", agent_id="mcp_browser", query="q",
                ),
                SubTask(
                    id=2, description="Draft email", agent_id="email_drafter",
                    query="q", depends_on=[1],
                ),
            ],
        )
        mock_plan.return_value = plan

        mock_execute.return_value = [
            SubTaskResult(
                subtask_id=1, description="Research", agent_id="mcp_browser",
                success=True, output="Found Q4 data",
                actions=[{"type": "mcp_tool_called"}],
            ),
            SubTaskResult(
                subtask_id=2, description="Draft email",
                agent_id="email_drafter",
                success=True, output="Draft: Dear Board...",
                actions=[{"type": "email_draft_created"}],
            ),
        ]

        # Synthesis response
        mock_llm.return_value = "I found the Q4 metrics and drafted an email."

        agent = OrchestratorAgent()
        store = MagicMock()
        config = GivaConfig()
        result = agent.execute("Research Q4 and email the board", {}, store, config)

        assert result.success is True
        assert "Q4" in result.output or "email" in result.output.lower()
        # Should have actions from subtasks + orchestration_complete
        action_types = [a["type"] for a in result.actions]
        assert "mcp_tool_called" in action_types
        assert "email_draft_created" in action_types
        assert "orchestration_complete" in action_types
        # Artifacts should contain plan metadata
        assert "plan" in result.artifacts

    @patch(_AGENT_PLAN, return_value=None)
    def test_plan_failure_returns_error(self, _mock_plan):
        agent = OrchestratorAgent()
        store = MagicMock()
        config = GivaConfig()
        result = agent.execute("do complex thing", {}, store, config)

        assert result.success is False
        assert "rephrase" in result.output.lower() or "couldn't" in result.output.lower()
        assert result.error is not None

    @patch(_AGENT_VALIDATE, return_value=(False, "Subtask 1 references unknown agent"))
    @patch(_AGENT_PLAN)
    def test_invalid_plan_returns_error(self, mock_plan, _mock_validate):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[SubTask(id=1, description="do", agent_id="bad", query="q")],
        )
        mock_plan.return_value = plan

        agent = OrchestratorAgent()
        store = MagicMock()
        config = GivaConfig()
        result = agent.execute("do something", {}, store, config)

        assert result.success is False
        assert "unknown agent" in result.output.lower()

    @patch("giva.agents.orchestrator.agent.OrchestratorAgent._llm_generate")
    @patch(_AGENT_EXECUTE)
    @patch(_AGENT_VALIDATE, return_value=(True, ""))
    @patch(_AGENT_PLAN)
    def test_mixed_success_failure(
        self, mock_plan, _mock_validate, mock_execute, mock_llm,
    ):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Step 1", agent_id="a", query="q"),
                SubTask(id=2, description="Step 2", agent_id="b", query="q"),
            ],
        )
        mock_plan.return_value = plan

        mock_execute.return_value = [
            SubTaskResult(
                subtask_id=1, description="Step 1", agent_id="a",
                success=True, output="Step 1 done",
            ),
            SubTaskResult(
                subtask_id=2, description="Step 2", agent_id="b",
                success=False, output="", error="Failed to connect",
            ),
        ]

        mock_llm.return_value = "Step 1 completed. Step 2 failed due to connection."

        agent = OrchestratorAgent()
        store = MagicMock()
        config = GivaConfig()
        result = agent.execute("do two things", {}, store, config)

        # Still success because at least one subtask succeeded
        assert result.success is True
        meta = next(
            a for a in result.actions if a["type"] == "orchestration_complete"
        )
        assert meta["succeeded"] == 1
        assert meta["failed"] == 1

    @patch(_AGENT_EXECUTE, return_value=[])
    @patch(_AGENT_VALIDATE, return_value=(True, ""))
    @patch(_AGENT_PLAN)
    def test_empty_results_synthesis(self, mock_plan, _mock_validate, _mock_execute):
        plan = OrchestratorPlan(goal="test", subtasks=[])
        mock_plan.return_value = plan

        agent = OrchestratorAgent()
        store = MagicMock()
        config = GivaConfig()
        result = agent.execute("do something", {}, store, config)

        assert result.success is True
        assert "no subtasks" in result.output.lower()

    @patch("giva.agents.orchestrator.agent.OrchestratorAgent._llm_generate")
    @patch(_AGENT_EXECUTE)
    @patch(_AGENT_VALIDATE, return_value=(True, ""))
    @patch(_AGENT_PLAN)
    def test_synthesis_fallback_on_llm_failure(
        self, mock_plan, _mock_validate, mock_execute, mock_llm,
    ):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Step 1", agent_id="a", query="q"),
            ],
        )
        mock_plan.return_value = plan

        mock_execute.return_value = [
            SubTaskResult(
                subtask_id=1, description="Step 1", agent_id="a",
                success=True, output="Result text here",
            ),
        ]

        # Planning call succeeds, synthesis call fails
        mock_llm.side_effect = [
            # generate_plan calls _llm_generate → but we've mocked generate_plan
            # _synthesize calls _llm_generate → fails
            RuntimeError("model crashed"),
        ]

        agent = OrchestratorAgent()
        store = MagicMock()
        config = GivaConfig()
        result = agent.execute("do something", {}, store, config)

        # Should fall back to raw results
        assert result.success is True
        assert "Step 1" in result.output
        assert "Result text" in result.output
