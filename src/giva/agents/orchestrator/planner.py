"""Task decomposition: converts a complex query into an OrchestratorPlan.

Uses the assistant model to analyse the user request, builds an agent catalog
from the registry, and generates a structured plan with dependency ordering.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from giva.agents.orchestrator.prompts import PLAN_SYSTEM, PLAN_USER
from giva.agents.registry import registry
from giva.config import GivaConfig
from giva.llm.structured import OrchestratorPlan

log = logging.getLogger(__name__)


def generate_plan(
    query: str,
    config: GivaConfig,
    llm_generate_fn: Callable,
    max_subtasks: int = 6,
) -> Optional[OrchestratorPlan]:
    """Generate an execution plan from a user query.

    Args:
        query: The user's complex request.
        config: Giva configuration.
        llm_generate_fn: Bound ``BaseAgent._llm_generate`` method.
        max_subtasks: Maximum subtasks allowed in the plan.

    Returns:
        OrchestratorPlan or None if planning fails.
    """
    # Build agent catalog, excluding the orchestrator itself
    catalog_lines: list[str] = []
    for m in registry.list_manifests():
        if m.agent_id == "orchestrator":
            continue
        note = " (requires user confirmation)" if m.requires_confirmation else ""
        catalog_lines.append(f"- {m.agent_id}: {m.name} — {m.description}{note}")
        for ex in m.examples[:2]:
            catalog_lines.append(f'    example: "{ex}"')

    if not catalog_lines:
        log.warning("No agents available for orchestration")
        return None

    catalog = "\n".join(catalog_lines)

    system = PLAN_SYSTEM.format(agent_catalog=catalog, max_subtasks=max_subtasks)
    user = PLAN_USER.format(query=query[:1000])

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        raw = llm_generate_fn(config, messages, max_tokens=1024, temp=0.3)
        plan = _parse_plan(raw)
        if plan and len(plan.subtasks) > max_subtasks:
            plan = OrchestratorPlan(
                goal=plan.goal,
                reasoning=plan.reasoning,
                subtasks=plan.subtasks[:max_subtasks],
            )
        return plan
    except Exception as e:
        log.error("Plan generation failed: %s", e)
        return None


def validate_plan(plan: OrchestratorPlan) -> tuple[bool, str]:
    """Validate a generated plan for structural correctness.

    Returns ``(is_valid, error_message)``.  Empty error means valid.
    """
    if not plan.subtasks:
        return False, "Plan has no subtasks"

    ids = {st.id for st in plan.subtasks}

    # Duplicate IDs
    if len(ids) != len(plan.subtasks):
        return False, "Duplicate subtask IDs"

    for st in plan.subtasks:
        # Recursion guard
        if st.agent_id == "orchestrator":
            return False, f"Subtask {st.id} tries to call orchestrator (recursion)"

        # Agent existence
        if registry.get(st.agent_id) is None:
            return False, f"Subtask {st.id} references unknown agent '{st.agent_id}'"

        # Dependency references
        for dep in st.depends_on:
            if dep not in ids:
                return False, (
                    f"Subtask {st.id} depends on nonexistent subtask {dep}"
                )
            if dep >= st.id:
                return False, (
                    f"Subtask {st.id} depends on later subtask {dep} (cycle risk)"
                )

    # Cycle detection via topological sort feasibility
    if not _can_topologically_sort(plan.subtasks):
        return False, "Dependency cycle detected"

    return True, ""


def topological_sort(subtasks: list) -> list:
    """Sort subtasks by dependency order (Kahn's algorithm).

    Deterministic: among peers with equal in-degree, lower IDs come first.
    Returns a new list.
    """
    in_degree = {st.id: len(st.depends_on) for st in subtasks}
    by_id = {st.id: st for st in subtasks}
    queue = sorted(sid for sid, deg in in_degree.items() if deg == 0)
    result: list = []

    while queue:
        current = queue.pop(0)
        result.append(by_id[current])
        for st in subtasks:
            if current in st.depends_on:
                in_degree[st.id] -= 1
                if in_degree[st.id] == 0:
                    # Insert sorted to keep deterministic order
                    queue.append(st.id)
                    queue.sort()

    return result


def format_plan_summary(plan: OrchestratorPlan) -> str:
    """Format a plan as a human-readable summary for the confirmation message.

    Designed to be included in the ``agent_confirm`` SSE event so the user
    can approve the specific scope before execution begins.
    """
    lines = [f"**Plan**: {plan.goal}"]
    if plan.reasoning:
        lines.append(f"_{plan.reasoning}_")
    lines.append("")

    for st in plan.subtasks:
        dep_note = ""
        if st.depends_on:
            dep_note = f" (after step{'s' if len(st.depends_on) > 1 else ''} " + \
                ", ".join(str(d) for d in st.depends_on) + ")"
        lines.append(f"{st.id}. **{st.description}** → `{st.agent_id}`{dep_note}")

    lines.append("")
    lines.append("Shall I proceed with this plan?")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _can_topologically_sort(subtasks: list) -> bool:
    """Check if subtasks can be topologically sorted (no cycles)."""
    return len(topological_sort(subtasks)) == len(subtasks)


def _parse_plan(raw: str) -> Optional[OrchestratorPlan]:
    """Parse an OrchestratorPlan from LLM output with fail-safe JSON extraction.

    Follows the project pattern: direct parse → markdown code block → regex.
    """
    raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)

    # Try direct parse
    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict):
            return OrchestratorPlan.model_validate(parsed)
    except (json.JSONDecodeError, Exception):
        pass

    # Extract from markdown code block
    md_match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if md_match:
        try:
            parsed = json.loads(md_match.group(1).strip())
            if isinstance(parsed, dict):
                return OrchestratorPlan.model_validate(parsed)
        except (json.JSONDecodeError, Exception):
            pass

    # Extract first JSON object
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, dict):
                return OrchestratorPlan.model_validate(parsed)
        except (json.JSONDecodeError, Exception):
            pass

    log.debug("Failed to parse orchestrator plan: %s", raw[:200])
    return None
