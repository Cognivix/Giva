"""Tests for the orchestrator planner module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from giva.agents.base import AgentManifest
from giva.agents.orchestrator.planner import (
    format_plan_summary,
    generate_plan,
    topological_sort,
    validate_plan,
)
from giva.config import GivaConfig
from giva.llm.structured import OrchestratorPlan, SubTask

_PLANNER_REG = "giva.agents.orchestrator.planner.registry"


def _mock_manifests():
    """Return sample manifests for testing."""
    return [
        AgentManifest(
            agent_id="email_drafter",
            name="Email Drafter",
            description="Drafts professional emails",
            examples=["Draft an email to Bob"],
            model_tier="assistant",
        ),
        AgentManifest(
            agent_id="mcp_browser",
            name="MCP: Browser",
            description="Browse the web and extract information",
            examples=["Search for Q4 metrics"],
            model_tier="none",
        ),
    ]


# ---------------------------------------------------------------------------
# validate_plan tests
# ---------------------------------------------------------------------------


class TestValidatePlan:
    def test_valid_linear_plan(self):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="A", agent_id="email_drafter", query="q"),
                SubTask(
                    id=2, description="B", agent_id="email_drafter",
                    query="q", depends_on=[1],
                ),
            ],
        )
        with patch(_PLANNER_REG) as mock_reg:
            mock_reg.get.return_value = MagicMock()
            valid, err = validate_plan(plan)
        assert valid is True
        assert err == ""

    def test_empty_subtasks(self):
        plan = OrchestratorPlan(goal="test", subtasks=[])
        valid, err = validate_plan(plan)
        assert valid is False
        assert "no subtasks" in err.lower()

    def test_duplicate_ids(self):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="A", agent_id="email_drafter", query="q"),
                SubTask(id=1, description="B", agent_id="email_drafter", query="q"),
            ],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "Duplicate" in err

    def test_self_reference_blocked(self):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="A", agent_id="orchestrator", query="q"),
            ],
        )
        valid, err = validate_plan(plan)
        assert valid is False
        assert "recursion" in err.lower()

    def test_missing_agent(self):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="A", agent_id="nonexistent", query="q"),
            ],
        )
        with patch(_PLANNER_REG) as mock_reg:
            mock_reg.get.return_value = None
            valid, err = validate_plan(plan)
        assert valid is False
        assert "unknown agent" in err.lower()

    def test_nonexistent_dependency(self):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(
                    id=1, description="A", agent_id="email_drafter",
                    query="q", depends_on=[99],
                ),
            ],
        )
        with patch(_PLANNER_REG) as mock_reg:
            mock_reg.get.return_value = MagicMock()
            valid, err = validate_plan(plan)
        assert valid is False
        assert "nonexistent" in err.lower()

    def test_forward_dependency_rejected(self):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(
                    id=1, description="A", agent_id="email_drafter",
                    query="q", depends_on=[2],
                ),
                SubTask(id=2, description="B", agent_id="email_drafter", query="q"),
            ],
        )
        with patch(_PLANNER_REG) as mock_reg:
            mock_reg.get.return_value = MagicMock()
            valid, err = validate_plan(plan)
        assert valid is False
        assert "later subtask" in err.lower()

    def test_single_subtask_valid(self):
        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="A", agent_id="email_drafter", query="q"),
            ],
        )
        with patch(_PLANNER_REG) as mock_reg:
            mock_reg.get.return_value = MagicMock()
            valid, err = validate_plan(plan)
        assert valid is True


# ---------------------------------------------------------------------------
# topological_sort tests
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_linear_chain(self):
        subtasks = [
            SubTask(
                id=3, description="C", agent_id="x", query="q", depends_on=[2],
            ),
            SubTask(id=1, description="A", agent_id="x", query="q"),
            SubTask(
                id=2, description="B", agent_id="x", query="q", depends_on=[1],
            ),
        ]
        result = topological_sort(subtasks)
        ids = [st.id for st in result]
        assert ids == [1, 2, 3]

    def test_independent_tasks_sorted_by_id(self):
        subtasks = [
            SubTask(id=3, description="C", agent_id="x", query="q"),
            SubTask(id=1, description="A", agent_id="x", query="q"),
            SubTask(id=2, description="B", agent_id="x", query="q"),
        ]
        result = topological_sort(subtasks)
        ids = [st.id for st in result]
        assert ids == [1, 2, 3]

    def test_diamond_dependency(self):
        subtasks = [
            SubTask(id=1, description="A", agent_id="x", query="q"),
            SubTask(
                id=2, description="B", agent_id="x", query="q", depends_on=[1],
            ),
            SubTask(
                id=3, description="C", agent_id="x", query="q", depends_on=[1],
            ),
            SubTask(
                id=4, description="D", agent_id="x", query="q",
                depends_on=[2, 3],
            ),
        ]
        result = topological_sort(subtasks)
        ids = [st.id for st in result]
        assert ids[0] == 1
        assert ids[-1] == 4
        assert set(ids[1:3]) == {2, 3}

    def test_single_task(self):
        subtasks = [SubTask(id=1, description="A", agent_id="x", query="q")]
        result = topological_sort(subtasks)
        assert len(result) == 1
        assert result[0].id == 1


# ---------------------------------------------------------------------------
# generate_plan tests
# ---------------------------------------------------------------------------


class TestGeneratePlan:
    @patch(_PLANNER_REG)
    def test_generates_valid_plan(self, mock_reg):
        mock_reg.list_manifests.return_value = _mock_manifests()

        plan_json = json.dumps({
            "goal": "Research and email",
            "reasoning": "Two steps needed",
            "subtasks": [
                {
                    "id": 1,
                    "description": "Research metrics",
                    "agent_id": "mcp_browser",
                    "query": "Find Q4 metrics",
                    "params": {},
                    "depends_on": [],
                },
                {
                    "id": 2,
                    "description": "Draft email",
                    "agent_id": "email_drafter",
                    "query": "Draft summary email",
                    "params": {},
                    "depends_on": [1],
                },
            ],
        })

        mock_llm = MagicMock(return_value=plan_json)
        config = GivaConfig()
        plan = generate_plan("Research Q4 and email the board", config, mock_llm)

        assert plan is not None
        assert plan.goal == "Research and email"
        assert len(plan.subtasks) == 2
        assert plan.subtasks[0].agent_id == "mcp_browser"
        assert plan.subtasks[1].depends_on == [1]

    @patch(_PLANNER_REG)
    def test_no_agents_returns_none(self, mock_reg):
        mock_reg.list_manifests.return_value = []

        mock_llm = MagicMock()
        config = GivaConfig()
        plan = generate_plan("do something", config, mock_llm)

        assert plan is None
        mock_llm.assert_not_called()

    @patch(_PLANNER_REG)
    def test_llm_failure_returns_none(self, mock_reg):
        mock_reg.list_manifests.return_value = _mock_manifests()

        mock_llm = MagicMock(side_effect=RuntimeError("model not loaded"))
        config = GivaConfig()
        plan = generate_plan("do something", config, mock_llm)

        assert plan is None

    @patch(_PLANNER_REG)
    def test_parses_json_in_markdown_block(self, mock_reg):
        mock_reg.list_manifests.return_value = _mock_manifests()

        raw = '```json\n{"goal": "test", "subtasks": [' \
              '{"id": 1, "description": "do", "agent_id": "email_drafter", ' \
              '"query": "q"}]}\n```'

        mock_llm = MagicMock(return_value=raw)
        config = GivaConfig()
        plan = generate_plan("test", config, mock_llm)

        assert plan is not None
        assert plan.goal == "test"

    @patch(_PLANNER_REG)
    def test_truncates_excess_subtasks(self, mock_reg):
        mock_reg.list_manifests.return_value = _mock_manifests()

        subtasks = [
            {"id": i, "description": f"Step {i}", "agent_id": "email_drafter",
             "query": "q"}
            for i in range(1, 10)  # 9 subtasks — exceeds default max of 6
        ]
        plan_json = json.dumps({"goal": "big plan", "subtasks": subtasks})
        mock_llm = MagicMock(return_value=plan_json)

        config = GivaConfig()
        plan = generate_plan("big thing", config, mock_llm, max_subtasks=6)

        assert plan is not None
        assert len(plan.subtasks) == 6

    @patch(_PLANNER_REG)
    def test_excludes_orchestrator_from_catalog(self, mock_reg):
        manifests = _mock_manifests() + [
            AgentManifest(
                agent_id="orchestrator",
                name="Task Orchestrator",
                description="Meta-agent",
            ),
        ]
        mock_reg.list_manifests.return_value = manifests

        plan_json = json.dumps({
            "goal": "test",
            "subtasks": [
                {"id": 1, "description": "do", "agent_id": "email_drafter",
                 "query": "q"},
            ],
        })
        mock_llm = MagicMock(return_value=plan_json)
        config = GivaConfig()

        generate_plan("test", config, mock_llm)

        # The agent catalog section must not list orchestrator as an available agent.
        # (The prompt *rules* mention "orchestrator" to forbid recursion — that's fine.)
        call_args = mock_llm.call_args
        messages = call_args[0][1]  # (config, messages, ...)
        system_prompt = messages[0]["content"]
        assert "- orchestrator:" not in system_prompt


# ---------------------------------------------------------------------------
# format_plan_summary tests
# ---------------------------------------------------------------------------


class TestFormatPlanSummary:
    def test_basic_format(self):
        plan = OrchestratorPlan(
            goal="Research and email the board",
            reasoning="Two-step process",
            subtasks=[
                SubTask(
                    id=1, description="Research Q4 metrics",
                    agent_id="mcp_browser", query="q",
                ),
                SubTask(
                    id=2, description="Draft summary email",
                    agent_id="email_drafter", query="q", depends_on=[1],
                ),
            ],
        )
        summary = format_plan_summary(plan)
        assert "Research and email the board" in summary
        assert "Research Q4 metrics" in summary
        assert "Draft summary email" in summary
        assert "mcp_browser" in summary
        assert "email_drafter" in summary
        assert "after step 1" in summary
        assert "Shall I proceed" in summary
