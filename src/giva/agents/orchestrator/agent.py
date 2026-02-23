"""OrchestratorAgent: decomposes complex requests into multi-agent workflows.

Acts as planner, delegator, and QA reviewer:
1. PLAN  — assistant model decomposes query into subtasks
2. VALIDATE — structural checks (agents exist, no cycles, no recursion)
3. EXECUTE — subtasks run sequentially via execute_agent()
4. QA    — filter model evaluates each subtask output
5. SYNTHESIZE — assistant model combines results into coherent response

Lock discipline:
    ``model_tier="assistant"`` so the server holds ``_llm_lock`` during
    ``execute()``.  All internal LLM calls (planning, sub-agent execution,
    QA, synthesis) run on the same thread — no lock re-acquisition, no
    deadlock risk.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from giva.agents.base import AgentManifest, AgentResult, BaseAgent
from giva.agents.orchestrator.executor import SubTaskResult, execute_plan
from giva.agents.orchestrator.planner import generate_plan, validate_plan
from giva.agents.orchestrator.prompts import SYNTHESIZE_SYSTEM, SYNTHESIZE_USER
from giva.config import GivaConfig
from giva.db.store import Store
from giva.llm.structured import OrchestratorPlan

log = logging.getLogger(__name__)


class OrchestratorAgent(BaseAgent):
    """Meta-agent that decomposes complex queries into multi-step plans.

    The orchestrator collects "carte blanche" from the user by presenting
    its plan during the confirmation phase.  Once confirmed, all sub-agents
    execute freely within the approved scope — no per-subtask confirmations.

    **Does NOT call itself** — the planner prompt and validate_plan() both
    prevent recursion.
    """

    def __init__(self):
        super().__init__(AgentManifest(
            agent_id="orchestrator",
            name="Task Orchestrator",
            description=(
                "Handles complex multi-step requests by decomposing them into "
                "subtasks and delegating to specialized agents. Use for requests "
                "that require multiple agents to work together."
            ),
            examples=[
                "Research the Q4 metrics and draft a summary email to the board",
                "Check my recent emails about the project, create tasks for "
                "action items, and draft replies",
                "Look up flight options and draft an email to my team about the trip",
                "Review my goals, suggest updates, and create tasks for next steps",
                "Find information about the client meeting and prepare a follow-up email",
            ],
            model_tier="assistant",
            supports_streaming=False,
            requires_confirmation=True,
            version="0.1.0",
        ))

    # ---- public interface ---------------------------------------------------

    def execute(
        self,
        query: str,
        context: dict,
        store: Store,
        config: GivaConfig,
    ) -> AgentResult:
        """Decompose, delegate, QA, and synthesize."""
        max_subtasks = config.agents.orchestrator_max_subtasks
        deadline = time.monotonic() + config.agents.orchestrator_timeout_seconds

        # 1. PLAN
        log.info("Orchestrator: generating plan for: %s", query[:100])
        plan = generate_plan(query, config, self._llm_generate, max_subtasks)

        if plan is None:
            return AgentResult(
                success=False,
                output=(
                    "I couldn't break down this request into actionable steps. "
                    "Could you rephrase or simplify?"
                ),
                error="Plan generation failed",
            )

        # 2. VALIDATE
        valid, error_msg = validate_plan(plan)
        if not valid:
            log.warning("Orchestrator: invalid plan — %s", error_msg)
            return AgentResult(
                success=False,
                output=f"I created a plan but found an issue: {error_msg}. "
                       "Could you rephrase?",
                error=f"Plan validation failed: {error_msg}",
            )

        log.info(
            "Orchestrator: plan with %d subtasks: %s",
            len(plan.subtasks),
            ", ".join(f"{st.id}:{st.agent_id}" for st in plan.subtasks),
        )

        # 3. EXECUTE
        subtask_results = execute_plan(
            plan, context, store, config, self._llm_generate, deadline,
        )

        # 4. SYNTHESIZE
        output = self._synthesize(query, subtask_results, config)

        # 5. AGGREGATE actions and artifacts
        all_actions: list[dict] = []
        all_artifacts: dict = {}
        any_success = False

        for sr in subtask_results:
            if sr.success:
                any_success = True
            all_actions.extend(sr.actions)
            if sr.artifacts:
                all_artifacts[f"subtask_{sr.subtask_id}"] = sr.artifacts

        # Orchestration metadata
        all_actions.append({
            "type": "orchestration_complete",
            "plan_goal": plan.goal,
            "subtask_count": len(plan.subtasks),
            "succeeded": sum(1 for sr in subtask_results if sr.success),
            "failed": sum(1 for sr in subtask_results if not sr.success),
        })

        all_artifacts["plan"] = {
            "goal": plan.goal,
            "reasoning": plan.reasoning,
            "subtask_ids": [st.id for st in plan.subtasks],
        }

        return AgentResult(
            success=any_success or len(subtask_results) == 0,
            output=output,
            actions=all_actions,
            artifacts=all_artifacts,
        )

    def plan_only(
        self, query: str, config: GivaConfig,
    ) -> Optional[OrchestratorPlan]:
        """Generate and validate a plan without executing it.

        Called by the server during the confirmation phase to present the
        specific scope to the user before they approve.
        """
        max_subtasks = config.agents.orchestrator_max_subtasks
        plan = generate_plan(query, config, self._llm_generate, max_subtasks)
        if plan is None:
            return None
        valid, _ = validate_plan(plan)
        if not valid:
            return None
        return plan

    # ---- internal -----------------------------------------------------------

    def _synthesize(
        self,
        original_query: str,
        results: list[SubTaskResult],
        config: GivaConfig,
    ) -> str:
        """Combine subtask results into a single user-facing response."""
        if not results:
            return "I created a plan but there were no subtasks to execute."

        # Build results summary for the synthesis prompt
        result_lines: list[str] = []
        for sr in results:
            status = "COMPLETED" if sr.success else "FAILED"
            line = f"Step {sr.subtask_id} ({sr.description}) [{status}]"
            if sr.success:
                output_text = sr.output[:600]
                if len(sr.output) > 600:
                    output_text += "...[truncated]"
                line += f"\nOutput: {output_text}"
            elif sr.error:
                line += f"\nError: {sr.error}"
            result_lines.append(line)

        subtask_results_text = "\n\n".join(result_lines)

        system = SYNTHESIZE_SYSTEM.format(
            original_query=original_query[:500],
            subtask_results=subtask_results_text,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": SYNTHESIZE_USER},
        ]

        try:
            return self._llm_generate(config, messages, max_tokens=1024, temp=0.5)
        except Exception as e:
            log.error("Orchestrator synthesis failed: %s", e)
            # Fallback: return raw results
            fallback_lines: list[str] = []
            for sr in results:
                if sr.success:
                    fallback_lines.append(
                        f"**{sr.description}**: {sr.output[:300]}"
                    )
                else:
                    fallback_lines.append(
                        f"**{sr.description}**: Failed — {sr.error}"
                    )
            return "\n\n".join(fallback_lines)
