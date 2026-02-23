"""Tests for the orchestrator executor module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from giva.agents.base import AgentResult
from giva.agents.orchestrator.executor import (
    SubTaskResult,
    _enrich_query,
    execute_plan,
)
from giva.config import GivaConfig
from giva.llm.structured import OrchestratorPlan, SubTask

_EXECUTOR_EXEC = "giva.agents.orchestrator.executor.execute_agent"
_EXECUTOR_QA = "giva.agents.orchestrator.executor._run_qa"


# ---------------------------------------------------------------------------
# _enrich_query tests
# ---------------------------------------------------------------------------


class TestEnrichQuery:
    def test_no_dependencies(self):
        assert _enrich_query("do thing", [], {}) == "do thing"

    def test_with_dependency_output(self):
        results = {
            1: SubTaskResult(
                subtask_id=1, description="Research", agent_id="x",
                success=True, output="Found data XYZ",
            ),
        }
        enriched = _enrich_query("Draft based on research", [1], results)
        assert "Found data XYZ" in enriched
        assert "Draft based on research" in enriched
        assert "step 1" in enriched.lower()

    def test_failed_dependency_excluded(self):
        results = {
            1: SubTaskResult(
                subtask_id=1, description="Research", agent_id="x",
                success=False, output="", error="Failed",
            ),
        }
        enriched = _enrich_query("Draft based on research", [1], results)
        assert enriched == "Draft based on research"

    def test_long_output_truncated(self):
        long_output = "x" * 2000
        results = {
            1: SubTaskResult(
                subtask_id=1, description="Research", agent_id="x",
                success=True, output=long_output,
            ),
        }
        enriched = _enrich_query("query", [1], results)
        assert "[truncated]" in enriched
        # Should not contain the full 2000 chars
        assert len(enriched) < 1500


# ---------------------------------------------------------------------------
# execute_plan tests
# ---------------------------------------------------------------------------


class TestExecutePlan:
    @patch(_EXECUTOR_QA, return_value=None)  # Skip QA
    @patch(_EXECUTOR_EXEC)
    def test_single_subtask_success(self, mock_exec, _mock_qa):
        mock_exec.return_value = AgentResult(
            success=True, output="Done successfully",
        )

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Do X", agent_id="test_agent", query="q"),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        deadline = time.monotonic() + 60

        results = execute_plan(plan, {}, store, config, MagicMock(), deadline)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == "Done successfully"
        mock_exec.assert_called_once()

    @patch(_EXECUTOR_QA, return_value=None)
    @patch(_EXECUTOR_EXEC)
    def test_dependency_chain_enriches_query(self, mock_exec, _mock_qa):
        mock_exec.side_effect = [
            AgentResult(success=True, output="Research result ABC"),
            AgentResult(success=True, output="Draft done"),
        ]

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Research", agent_id="a", query="research"),
                SubTask(
                    id=2, description="Draft", agent_id="a",
                    query="draft email", depends_on=[1],
                ),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        deadline = time.monotonic() + 60

        results = execute_plan(plan, {}, store, config, MagicMock(), deadline)

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is True

        # Second call should have enriched query containing step 1 output
        second_call_query = mock_exec.call_args_list[1][0][1]  # positional arg 1
        assert "Research result ABC" in second_call_query

    @patch(_EXECUTOR_QA, return_value=None)
    @patch(_EXECUTOR_EXEC)
    def test_dependency_failure_skips_dependent(self, mock_exec, _mock_qa):
        mock_exec.return_value = AgentResult(
            success=False, output="", error="Boom",
        )

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Step 1", agent_id="a", query="q"),
                SubTask(
                    id=2, description="Step 2", agent_id="a",
                    query="q", depends_on=[1],
                ),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        deadline = time.monotonic() + 60

        results = execute_plan(plan, {}, store, config, MagicMock(), deadline)

        assert len(results) == 2
        assert results[0].success is False
        assert results[1].success is False
        assert "Skipped" in results[1].error

        # execute_agent called only once (step 2 skipped)
        assert mock_exec.call_count == 1

    @patch(_EXECUTOR_QA, return_value=None)
    @patch(_EXECUTOR_EXEC)
    def test_timeout_skips_remaining(self, mock_exec, _mock_qa):
        mock_exec.return_value = AgentResult(success=True, output="ok")

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Step 1", agent_id="a", query="q"),
                SubTask(id=2, description="Step 2", agent_id="a", query="q"),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        # Deadline already passed
        deadline = time.monotonic() - 1

        results = execute_plan(plan, {}, store, config, MagicMock(), deadline)

        assert len(results) == 2
        assert all(not r.success for r in results)
        assert all("timeout" in r.error.lower() for r in results)
        mock_exec.assert_not_called()

    @patch(_EXECUTOR_EXEC)
    def test_qa_failure_triggers_retry(self, mock_exec):
        from giva.llm.structured import SubTaskQA

        mock_exec.side_effect = [
            AgentResult(
                success=True,
                output="First attempt output that is long enough for QA",
            ),
            AgentResult(
                success=True,
                output="Retry output with better content here",
            ),
        ]

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Do X", agent_id="a", query="q"),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        deadline = time.monotonic() + 60

        qa_result = SubTaskQA(
            passed=False, feedback="Missing details",
            retry_suggestion="q with more details",
        )
        with patch(_EXECUTOR_QA, return_value=qa_result):
            results = execute_plan(plan, {}, store, config, MagicMock(), deadline)

        assert len(results) == 1
        # Retry was attempted
        assert mock_exec.call_count == 2
        # Second call used retry_suggestion
        retry_query = mock_exec.call_args_list[1][0][1]
        assert "more details" in retry_query

    @patch(_EXECUTOR_EXEC)
    def test_qa_parse_failure_treated_as_pass(self, mock_exec):
        mock_exec.return_value = AgentResult(
            success=True,
            output="This is a sufficiently long output for QA to run",
        )

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Do X", agent_id="a", query="q"),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        deadline = time.monotonic() + 60

        # QA returns None (parse failure) → treated as pass
        with patch(_EXECUTOR_QA, return_value=None):
            results = execute_plan(plan, {}, store, config, MagicMock(), deadline)

        assert len(results) == 1
        assert results[0].success is True
        # No retry — only called once
        assert mock_exec.call_count == 1

    @patch(_EXECUTOR_QA, return_value=None)
    @patch(_EXECUTOR_EXEC)
    def test_agent_failure_returns_error(self, mock_exec, _mock_qa):
        mock_exec.return_value = AgentResult(
            success=False, output="", error="Connection refused",
        )

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(id=1, description="Call API", agent_id="a", query="q"),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        deadline = time.monotonic() + 60

        results = execute_plan(plan, {}, store, config, MagicMock(), deadline)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "Connection refused"

    @patch(_EXECUTOR_QA, return_value=None)
    @patch(_EXECUTOR_EXEC)
    def test_subtask_logging(self, mock_exec, _mock_qa):
        mock_exec.return_value = AgentResult(success=True, output="ok")

        plan = OrchestratorPlan(
            goal="test",
            subtasks=[
                SubTask(
                    id=1, description="Do X", agent_id="email_drafter", query="q",
                ),
            ],
        )
        config = GivaConfig()
        store = MagicMock()
        deadline = time.monotonic() + 60

        execute_plan(plan, {}, store, config, MagicMock(), deadline)

        store.log_agent_execution.assert_called_once()
        call_kwargs = store.log_agent_execution.call_args[1]
        assert "orchestrator>email_drafter" in call_kwargs["agent_id"]
