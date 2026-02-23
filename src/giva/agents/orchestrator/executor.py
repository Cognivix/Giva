"""Sequential subtask execution with QA validation.

Runs subtasks in topological (dependency) order, evaluates each output
with a lightweight QA check (filter model), and retries once on failure.
All calls happen on the same thread that already holds ``_llm_lock``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from giva.agents.orchestrator.planner import topological_sort
from giva.agents.orchestrator.prompts import QA_SYSTEM
from giva.agents.router import execute_agent
from giva.config import GivaConfig
from giva.db.store import Store
from giva.llm.structured import OrchestratorPlan, SubTaskQA

log = logging.getLogger(__name__)


@dataclass
class SubTaskResult:
    """Result of a single subtask execution within the orchestrated plan."""

    subtask_id: int
    description: str
    agent_id: str
    success: bool
    output: str
    actions: list[dict] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    error: Optional[str] = None
    qa_passed: bool = True
    qa_feedback: str = ""
    duration_ms: int = 0


def execute_plan(
    plan: OrchestratorPlan,
    context: dict,
    store: Store,
    config: GivaConfig,
    llm_generate_fn: Callable,
    deadline: float,
) -> list[SubTaskResult]:
    """Execute all subtasks in dependency order.

    Runs subtasks sequentially.  The server holds ``_llm_lock`` for the
    entire orchestrator ``execute()`` call, so all LLM calls (sub-agent
    executions, QA checks) are safe on this thread.

    Args:
        plan: Validated OrchestratorPlan.
        context: Original execution context dict.
        store: Data store.
        config: Giva configuration.
        llm_generate_fn: For QA evaluation calls.
        deadline: ``time.monotonic()`` deadline — skip remaining subtasks
            if exceeded.

    Returns:
        List of SubTaskResult objects in execution order.
    """
    ordered = topological_sort(plan.subtasks)
    results: dict[int, SubTaskResult] = {}
    result_list: list[SubTaskResult] = []

    for subtask in ordered:
        # Check wall-clock deadline
        if time.monotonic() > deadline:
            st_result = SubTaskResult(
                subtask_id=subtask.id,
                description=subtask.description,
                agent_id=subtask.agent_id,
                success=False,
                output="",
                error="Orchestrator timeout exceeded",
            )
            results[subtask.id] = st_result
            result_list.append(st_result)
            continue

        log.info(
            "Orchestrator: executing subtask %d (%s via %s)",
            subtask.id, subtask.description, subtask.agent_id,
        )

        # Check if all dependencies succeeded
        failed_deps = [
            dep_id for dep_id in subtask.depends_on
            if not results.get(dep_id, SubTaskResult(0, "", "", False, "")).success
        ]
        if failed_deps:
            st_result = SubTaskResult(
                subtask_id=subtask.id,
                description=subtask.description,
                agent_id=subtask.agent_id,
                success=False,
                output="",
                error=f"Skipped: dependencies {failed_deps} failed",
            )
            results[subtask.id] = st_result
            result_list.append(st_result)
            continue

        # Build enriched query with dependency outputs
        enriched_query = _enrich_query(subtask.query, subtask.depends_on, results)

        # Execute the subtask
        st_result = _execute_single(
            subtask, enriched_query, context, store, config, llm_generate_fn,
        )
        results[subtask.id] = st_result
        result_list.append(st_result)

        # Log to agent_executions table
        store.log_agent_execution(
            agent_id=f"orchestrator>{subtask.agent_id}",
            query=enriched_query[:500],
            params=subtask.params,
            success=st_result.success,
            output_summary=st_result.output[:500],
            artifacts=st_result.artifacts,
            error=st_result.error or "",
            duration_ms=st_result.duration_ms,
        )

    return result_list


def _execute_single(
    subtask,
    query: str,
    context: dict,
    store: Store,
    config: GivaConfig,
    llm_generate_fn: Callable,
) -> SubTaskResult:
    """Execute a single subtask with QA evaluation and one retry on failure."""
    sub_context = {
        "params": subtask.params,
        "query": query,
        "orchestrated": True,
    }

    start = time.monotonic()
    agent_result = execute_agent(subtask.agent_id, query, sub_context, store, config)
    duration_ms = int((time.monotonic() - start) * 1000)

    if not agent_result.success:
        return SubTaskResult(
            subtask_id=subtask.id,
            description=subtask.description,
            agent_id=subtask.agent_id,
            success=False,
            output=agent_result.output,
            actions=agent_result.actions,
            artifacts=agent_result.artifacts,
            error=agent_result.error,
            duration_ms=duration_ms,
        )

    # QA evaluation (skip for trivial outputs)
    if len(agent_result.output) < 20:
        return SubTaskResult(
            subtask_id=subtask.id,
            description=subtask.description,
            agent_id=subtask.agent_id,
            success=True,
            output=agent_result.output,
            actions=agent_result.actions,
            artifacts=agent_result.artifacts,
            duration_ms=duration_ms,
        )

    qa = _run_qa(subtask, agent_result.output, config)

    if qa is None or qa.passed:
        return SubTaskResult(
            subtask_id=subtask.id,
            description=subtask.description,
            agent_id=subtask.agent_id,
            success=True,
            output=agent_result.output,
            actions=agent_result.actions,
            artifacts=agent_result.artifacts,
            qa_passed=True,
            qa_feedback=qa.feedback if qa else "",
            duration_ms=duration_ms,
        )

    # QA failed — retry once with the suggestion
    log.info(
        "Orchestrator: QA failed for subtask %d, retrying. Feedback: %s",
        subtask.id, qa.feedback,
    )

    retry_query = qa.retry_suggestion or query
    retry_context = {**sub_context, "qa_feedback": qa.feedback}

    retry_start = time.monotonic()
    retry_result = execute_agent(
        subtask.agent_id, retry_query, retry_context, store, config,
    )
    retry_duration = int((time.monotonic() - retry_start) * 1000)

    return SubTaskResult(
        subtask_id=subtask.id,
        description=subtask.description,
        agent_id=subtask.agent_id,
        success=retry_result.success,
        output=retry_result.output,
        actions=retry_result.actions,
        artifacts=retry_result.artifacts,
        error=retry_result.error,
        qa_passed=retry_result.success,
        qa_feedback=qa.feedback,
        duration_ms=duration_ms + retry_duration,
    )


def _run_qa(subtask, output: str, config: GivaConfig) -> Optional[SubTaskQA]:
    """Run QA evaluation on a subtask's output using the filter model.

    Uses the filter model (not assistant) for speed — QA is classification,
    not synthesis.  Follows the "Model Assignment Rule" from the architecture
    doc.  Safe to call ``manager.generate()`` directly since the caller
    thread already holds ``_llm_lock``.
    """
    from giva.llm.engine import manager

    prompt = QA_SYSTEM.format(
        description=subtask.description,
        query=subtask.query[:300],
        output=output[:800],
    )
    messages = [{"role": "user", "content": prompt}]

    try:
        raw = manager.generate(
            config.llm.filter_model,
            messages,
            max_tokens=256,
            temp=0.1,
            top_p=0.9,
        )
        return _parse_qa(raw)
    except Exception as e:
        log.debug("QA evaluation failed: %s", e)
        return None  # Treat QA failure as "pass" — don't block execution


def _parse_qa(raw: str) -> Optional[SubTaskQA]:
    """Parse SubTaskQA from LLM output with fail-safe JSON extraction."""
    raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            return SubTaskQA.model_validate(parsed)
        except (json.JSONDecodeError, Exception):
            pass
    return None


def _enrich_query(
    query: str, depends_on: list[int], results: dict[int, SubTaskResult]
) -> str:
    """Prepend dependency outputs to the subtask query.

    When a subtask depends on prior steps, the prior outputs are prepended
    as context so the downstream agent has the information it needs.
    Long outputs are truncated to keep context manageable.
    """
    if not depends_on:
        return query

    context_parts: list[str] = []
    for dep_id in depends_on:
        dep_result = results.get(dep_id)
        if dep_result and dep_result.success and dep_result.output:
            output_text = dep_result.output[:1000]
            if len(dep_result.output) > 1000:
                output_text += "...[truncated]"
            context_parts.append(
                f"[Result from step {dep_id} ({dep_result.description})]: "
                f"{output_text}"
            )

    if not context_parts:
        return query

    return "\n\n".join(context_parts) + "\n\n" + query
