"""WebOrchestratorAgent: decomposes web tasks into VLM browser subtasks.

Bridges the Giva agent framework to the VLM task queue. When routed to by the
AgentRouter, it plans a sequence of visual browser actions and writes them to
the vlm_task_queue table. The Chrome extension then picks them up and executes
them via VLM inference.

The agent returns immediately after writing subtasks — it does NOT wait for VLM
execution. The Chrome extension polls /api/vlm/tasks/current and drives the
execution loop asynchronously.

Lock discipline:
    model_tier="assistant" so the server holds _llm_lock during execute().
    The agent releases the lock by returning; VLM execution also uses _llm_lock
    (all model operations are serialized via a single lock).
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlparse

from giva.agents.base import AgentManifest, AgentResult, BaseAgent
from giva.agents.web_orchestrator.prompts import PLAN_SYSTEM, PLAN_USER
from giva.config import GivaConfig
from giva.db.models import VlmTask
from giva.db.store import Store
from giva.llm.structured import WebPlan

log = logging.getLogger(__name__)


def _validate_plan(plan: WebPlan) -> tuple[bool, str]:
    """Validate a web plan: URLs well-formed, reasonable subtask count."""
    if not plan.subtasks:
        return False, "Plan has no subtasks"
    if len(plan.subtasks) > 8:
        return False, f"Too many subtasks ({len(plan.subtasks)}), max is 8"
    for i, st in enumerate(plan.subtasks):
        parsed = urlparse(st.target_url)
        if not parsed.scheme or not parsed.netloc:
            return False, f"Subtask {i}: invalid URL '{st.target_url}'"
        if not st.objective.strip():
            return False, f"Subtask {i}: empty objective"
    return True, ""


class WebOrchestratorAgent(BaseAgent):
    """Decomposes web tasks into VLM subtasks and writes them to the queue."""

    def __init__(self):
        super().__init__(AgentManifest(
            agent_id="web_orchestrator",
            name="Web Orchestrator",
            description=(
                "Handles web browser tasks by planning a sequence of visual "
                "actions and delegating to the VLM browser worker. Use for "
                "tasks that require navigating websites, clicking buttons, "
                "filling forms, or reading web content."
            ),
            examples=[
                "Review my LinkedIn messages and respond to Sarah",
                "Post about our new product launch on Twitter",
                "Check my GitHub notifications and star that repo",
                "Find the cheapest flight to NYC on Google Flights",
                "Fill out the expense report form on our company portal",
            ],
            model_tier="assistant",
            supports_streaming=False,
            requires_confirmation=True,
            version="0.1.0",
        ))

    def execute(
        self,
        query: str,
        context: dict,
        store: Store,
        config: GivaConfig,
    ) -> AgentResult:
        """Plan web task, write VLM subtasks to DB, return immediately."""
        job_id = context.get("job_id", "")
        goal_id = context.get("goal_id")

        # 0. PRE-CHECK — VLM tasks must link to a goal
        if goal_id is None:
            return AgentResult(
                success=False,
                output="Web tasks need to be linked to a goal. "
                       "Please create a goal first.",
                error="No goal_id in context",
            )

        # 1. PLAN — use assistant model to decompose
        log.info("WebOrchestrator: planning for: %s", query[:100])
        plan = self._generate_plan(query, config)

        if plan is None:
            return AgentResult(
                success=False,
                output=(
                    "I couldn't break down this web task into browser steps. "
                    "Could you be more specific about the website and action?"
                ),
                error="Web plan generation failed",
            )

        # 2. VALIDATE
        valid, error_msg = _validate_plan(plan)
        if not valid:
            log.warning("WebOrchestrator: invalid plan — %s", error_msg)
            return AgentResult(
                success=False,
                output=f"I created a plan but found an issue: {error_msg}. "
                       "Could you rephrase?",
                error=f"Plan validation failed: {error_msg}",
            )

        log.info(
            "WebOrchestrator: plan with %d subtasks for goal_id=%s",
            len(plan.subtasks), goal_id,
        )

        # 3. WRITE subtasks to vlm_task_queue
        task_ids = []
        for seq, subtask in enumerate(plan.subtasks):
            vlm_task = VlmTask(
                task_uuid=str(uuid.uuid4()),
                goal_id=goal_id,
                objective=subtask.objective,
                target_url=subtask.target_url,
                job_id=job_id,
                sequence=seq,
            )
            task_id = store.add_vlm_task(vlm_task)
            task_ids.append(task_id)
            log.info(
                "WebOrchestrator: wrote subtask %d/%d (id=%d): %s",
                seq + 1, len(plan.subtasks), task_id,
                subtask.objective[:60],
            )

        # 4. RETURN immediately — Chrome extension picks up from here
        subtask_summary = "\n".join(
            f"  {i+1}. {st.objective}" for i, st in enumerate(plan.subtasks)
        )
        return AgentResult(
            success=True,
            output=(
                f"Web task planned with {len(plan.subtasks)} steps:\n"
                f"{subtask_summary}\n\n"
                "The browser worker will execute these steps now. "
                "You'll be notified when complete."
            ),
            actions=[{
                "type": "vlm_plan_created",
                "plan_goal": plan.goal,
                "subtask_count": len(plan.subtasks),
                "task_ids": task_ids,
            }],
        )

    def _generate_plan(self, query: str, config: GivaConfig) -> WebPlan | None:
        """Use assistant model to generate a web task plan."""
        messages = [
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": PLAN_USER.format(query=query)},
        ]
        raw = self._llm_generate(config, messages, max_tokens=1024, temp=0.3)
        return self._parse_plan(raw)

    def _parse_plan(self, raw: str) -> WebPlan | None:
        """Parse LLM output into a WebPlan, with fallback extraction."""
        parsed = self._parse_json_safe(raw)
        if parsed is None:
            return None
        try:
            return WebPlan(**parsed)
        except Exception as e:
            log.warning("WebOrchestrator: failed to parse plan: %s", e)
            return None


AGENT_CLASS = WebOrchestratorAgent
