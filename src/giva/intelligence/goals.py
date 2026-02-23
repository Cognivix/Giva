"""Goal intelligence: inference, strategy generation, tactical planning, and progress tracking.

Provides functions to:
- Infer goals from user profile and email/calendar data
- Generate strategies for long-term goals
- Create tactical plans for mid-term objectives
- Track progress from sync data and chat interactions
- Build goal summaries for LLM context injection
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Generator

from giva.config import GivaConfig
from giva.db.models import Goal, GoalStrategy, Task
from giva.db.store import Store
from giva.llm.structured import (
    GoalInferenceResult,
    StrategyResult,
    TacticalPlan,
)

log = logging.getLogger(__name__)


def infer_goals(store: Store, config: GivaConfig) -> list[dict]:
    """Analyze user profile + recent data to infer goals.

    Returns list of inferred goal dicts for user confirmation.
    """
    from giva.intelligence.profile import get_profile_summary
    from giva.llm.engine import manager
    from giva.llm.prompts import (
        GOAL_INFER_SYSTEM,
        GOAL_INFER_USER,
        format_email_context,
        format_event_context,
    )

    profile_summary = get_profile_summary(store) or ""
    now_str = datetime.now().strftime("%A, %B %d, %Y")

    # Build context from recent data
    context_parts = []
    recent_emails = store.get_recent_emails(limit=20)
    if recent_emails:
        context_parts.append("Recent emails:\n" + format_email_context(recent_emails))

    upcoming = store.get_upcoming_events(days=14)
    if upcoming:
        context_parts.append("Upcoming events:\n" + format_event_context(upcoming))

    context = "\n\n".join(context_parts) if context_parts else "No data available."

    profile_section = f"User profile:\n{profile_summary}" if profile_summary else ""

    messages = [
        {
            "role": "system",
            "content": GOAL_INFER_SYSTEM.format(now=now_str, profile_section=profile_section),
        },
        {
            "role": "user",
            "content": GOAL_INFER_USER.format(context=context),
        },
    ]

    try:
        response = manager.generate(
            config.llm.model, messages, max_tokens=1024, temp=0.3, top_p=0.95
        )
        result = _parse_json_response(response, GoalInferenceResult)
        if result:
            return [g.model_dump() for g in result.goals]
    except Exception as e:
        log.warning("Goal inference failed: %s", e)

    return []


def generate_strategy(
    goal_id: int, store: Store, config: GivaConfig
) -> Generator[str, None, None]:
    """Generate a strategy for a goal. Yields streamed tokens.

    Also parses the structured output and saves a GoalStrategy row.
    """
    from giva.intelligence.profile import get_profile_summary
    from giva.llm.engine import manager
    from giva.llm.prompts import STRATEGY_SYSTEM, STRATEGY_USER

    goal = store.get_goal(goal_id)
    if not goal:
        yield "Goal not found."
        return

    profile_summary = get_profile_summary(store) or ""
    profile_section = f"User profile:\n{profile_summary}" if profile_summary else ""
    now_str = datetime.now().strftime("%A, %B %d, %Y")

    # Gather existing child objectives
    children = store.get_child_goals(goal_id)
    if children:
        obj_lines = [f"- {c.title} ({c.status})" for c in children]
        existing_objectives = "\n".join(obj_lines)
    else:
        existing_objectives = "None yet."

    system = STRATEGY_SYSTEM.format(
        now=now_str,
        profile_section=profile_section,
        goal_title=goal.title,
        goal_description=goal.description or "N/A",
        goal_category=goal.category or "general",
        goal_tier=goal.tier,
        target_date=goal.target_date.strftime("%Y-%m-%d") if goal.target_date else "Not set",
        existing_objectives=existing_objectives,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": STRATEGY_USER},
    ]

    full_response = []
    for token in manager.stream_generate(
        config.llm.model, messages, max_tokens=1024, temp=0.4, top_p=0.95
    ):
        full_response.append(token)
        yield token

    # Parse and save strategy
    text = "".join(full_response)
    result = _parse_json_response(text, StrategyResult)
    if result:
        strategy = GoalStrategy(
            goal_id=goal_id,
            strategy_text=result.approach,
            action_items=[a if isinstance(a, dict) else {"description": str(a)}
                          for a in result.action_items],
            suggested_objectives=[
                obj.model_dump() for obj in result.suggested_objectives
            ] if result.suggested_objectives else [],
        )
        store.add_strategy(strategy)
        log.info("Strategy saved for goal %d", goal_id)


def generate_tactical_plan(
    objective_id: int, store: Store, config: GivaConfig
) -> Generator[str, None, None]:
    """Generate a tactical plan for a mid-term objective. Yields streamed tokens."""
    from giva.intelligence.profile import get_profile_summary
    from giva.llm.engine import manager
    from giva.llm.prompts import (
        TACTICAL_PLAN_SYSTEM,
        TACTICAL_PLAN_USER,
        format_email_context,
        format_event_context,
    )

    objective = store.get_goal(objective_id)
    if not objective:
        yield "Objective not found."
        return

    profile_summary = get_profile_summary(store) or ""
    profile_section = f"User profile:\n{profile_summary}" if profile_summary else ""
    now_str = datetime.now().strftime("%A, %B %d, %Y")

    # Parent goal
    parent_title = "N/A"
    if objective.parent_id:
        parent = store.get_goal(objective.parent_id)
        if parent:
            parent_title = parent.title

    # Existing tasks for this goal
    existing_tasks_list = store.get_tasks_for_goal(objective_id)
    if existing_tasks_list:
        task_lines = [f"- [{t.priority}] {t.title} ({t.status})" for t in existing_tasks_list]
        existing_tasks = "\n".join(task_lines)
    else:
        existing_tasks = "None yet."

    # Search relevant emails by objective title keywords
    relevant_emails_list = store.search_emails(objective.title, limit=5)
    relevant_emails = format_email_context(relevant_emails_list)

    upcoming_events = store.get_upcoming_events(days=7)
    upcoming = format_event_context(upcoming_events)

    system = TACTICAL_PLAN_SYSTEM.format(
        now=now_str,
        profile_section=profile_section,
        objective_title=objective.title,
        objective_description=objective.description or "N/A",
        parent_goal_title=parent_title,
        target_date=(
            objective.target_date.strftime("%Y-%m-%d") if objective.target_date else "Not set"
        ),
        existing_tasks=existing_tasks,
        relevant_emails=relevant_emails,
        upcoming_events=upcoming,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": TACTICAL_PLAN_USER},
    ]

    full_response = []
    for token in manager.stream_generate(
        config.llm.model, messages, max_tokens=1024, temp=0.4, top_p=0.95
    ):
        full_response.append(token)
        yield token

    # Parse plan (saved externally via accept_plan)
    text = "".join(full_response)
    result = _parse_json_response(text, TacticalPlan)
    if result:
        log.info(
            "Tactical plan generated: %d tasks, %d email drafts, %d calendar blocks",
            len(result.tasks), len(result.email_drafts), len(result.calendar_blocks),
        )


def accept_plan(plan_json: str, objective_id: int, store: Store) -> int:
    """Create Task rows from a tactical plan JSON. Returns count of tasks created.

    Args:
        plan_json: Raw JSON string of the TacticalPlan response.
        objective_id: The goal_id to link tasks to.
        store: The data store.
    """
    result = _parse_json_response(plan_json, TacticalPlan)
    if not result:
        return 0

    count = 0
    for extracted in result.tasks:
        due = None
        if extracted.due_date:
            try:
                due = datetime.fromisoformat(extracted.due_date)
            except (ValueError, TypeError):
                pass

        task = Task(
            title=extracted.title,
            description=extracted.description or "",
            source_type="goal",
            source_id=objective_id,
            priority=(
                extracted.priority.value
                if hasattr(extracted.priority, "value")
                else extracted.priority
            ),
            due_date=due,
            goal_id=objective_id,
        )
        store.add_task(task)
        count += 1

    if count > 0:
        log.info("Created %d tasks from tactical plan for objective %d", count, objective_id)
    return count


def get_goals_summary(store: Store, include_progress: bool = False) -> str:
    """Build a compact hierarchical text summary of active goals.

    Used for LLM context injection in suggestions, queries, and reviews.
    """
    goals = store.get_goals(status="active")
    if not goals:
        return ""

    # Group by tier
    by_tier: dict[str, list[Goal]] = {"long_term": [], "mid_term": [], "short_term": []}
    for g in goals:
        by_tier.setdefault(g.tier, []).append(g)

    lines = []
    tier_labels = {
        "long_term": "Long-term Goals",
        "mid_term": "Mid-term Objectives",
        "short_term": "Short-term Tasks",
    }

    for tier in ["long_term", "mid_term", "short_term"]:
        tier_goals = by_tier.get(tier, [])
        if not tier_goals:
            continue

        lines.append(f"### {tier_labels[tier]}")
        for g in tier_goals:
            children = store.get_child_goals(g.id)
            child_str = f" ({len(children)} sub-objectives)" if children else ""
            tasks = store.get_tasks_for_goal(g.id)
            pending = [t for t in tasks if t.status == "pending"]
            task_str = f" [{len(pending)} pending tasks]" if pending else ""
            pri = f"[{g.priority.upper()}] " if g.priority == "high" else ""
            lines.append(f"- {pri}{g.title}{child_str}{task_str}")

            if include_progress:
                progress = store.get_goal_progress(g.id, limit=3)
                for p in progress:
                    date_str = (
                        p.created_at.strftime("%b %d") if p.created_at else "?"
                    )
                    lines.append(f"  > {date_str} [{p.source}]: {p.note}")

    return "\n".join(lines)


def create_initial_goals(store: Store, profile_data: dict) -> int:
    """Create Goal rows from onboarding-captured initial goals.

    Called after onboarding is marked complete.
    """
    initial = profile_data.get("initial_goals", [])
    count = 0
    for g in initial:
        title = g.get("title", "").strip()
        if not title:
            continue
        goal = Goal(
            title=title,
            tier=g.get("tier", "long_term"),
            category=g.get("category", ""),
            description=g.get("description", ""),
        )
        store.add_goal(goal)
        count += 1

    if count > 0:
        log.info("Created %d initial goals from onboarding", count)
    return count


def update_goal_progress_from_sync(store: Store, config: GivaConfig) -> int:
    """Analyze recently synced emails/events for goal-related progress signals.

    Uses the filter model (8B) for lightweight classification.
    Returns count of progress entries created.
    """
    from giva.llm.engine import manager
    from giva.llm.prompts import PROGRESS_DETECT_SYSTEM

    goals = store.get_goals(status="active")
    if not goals:
        return 0

    # Get emails synced in the last sync cycle (use sync_state)
    sync_state = store.get_sync_state("mail:INBOX")
    if not sync_state or not sync_state.get("last_sync"):
        return 0

    recent_emails = store.get_recent_emails(limit=10)
    if not recent_emails:
        return 0

    # Build goals list for prompt
    goals_list = "\n".join(
        f"- ID {g.id}: {g.title} ({g.tier}, {g.category})" for g in goals
    )

    # Build items block from recent emails
    items_lines = []
    for e in recent_emails[:10]:
        items_lines.append(f"- From: {e.from_name or e.from_addr}, Subject: {e.subject}")
    items = "\n".join(items_lines)

    messages = [
        {
            "role": "system",
            "content": PROGRESS_DETECT_SYSTEM.format(goals_list=goals_list, items=items),
        },
    ]

    try:
        response = manager.generate(
            config.llm.filter_model, messages, max_tokens=512, temp=0.2, top_p=0.9
        )
        updates = _parse_json_array(response)
        count = 0
        for update in updates:
            goal_id = update.get("goal_id")
            note = update.get("note", "")
            if goal_id and note and store.get_goal(goal_id):
                store.add_goal_progress(goal_id, note, "sync")
                count += 1
        return count
    except Exception as e:
        log.debug("Progress detection from sync failed: %s", e)
        return 0


def update_goal_progress_from_chat(
    query: str, response: str, store: Store, config: GivaConfig
) -> None:
    """Detect if a chat interaction relates to active goals and log progress.

    Uses keyword matching first, then LLM only if relevant.
    """
    goals = store.get_goals(status="active")
    if not goals:
        return

    # Quick keyword check: does the query/response mention any goal titles?
    combined = (query + " " + response).lower()
    relevant_goals = []
    for g in goals:
        # Check if goal title keywords appear in the conversation
        title_words = [w.lower() for w in g.title.split() if len(w) > 3]
        if any(w in combined for w in title_words):
            relevant_goals.append(g)

    if not relevant_goals:
        return

    # Use filter model for lightweight progress extraction
    from giva.llm.engine import manager
    from giva.llm.prompts import PROGRESS_DETECT_SYSTEM

    goals_list = "\n".join(
        f"- ID {g.id}: {g.title} ({g.tier}, {g.category})" for g in relevant_goals
    )
    items = f"User said: {query[:500]}\nAssistant replied: {response[:500]}"

    messages = [
        {
            "role": "system",
            "content": PROGRESS_DETECT_SYSTEM.format(goals_list=goals_list, items=items),
        },
    ]

    try:
        resp = manager.generate(
            config.llm.filter_model, messages, max_tokens=256, temp=0.2, top_p=0.9
        )
        updates = _parse_json_array(resp)
        for update in updates:
            goal_id = update.get("goal_id")
            note = update.get("note", "")
            if goal_id and note and store.get_goal(goal_id):
                store.add_goal_progress(goal_id, note, "chat")
    except Exception as e:
        log.debug("Progress detection from chat failed: %s", e)


# --- JSON parsing helpers ---


def _parse_json_response(response: str, model_cls):
    """Parse a JSON object from an LLM response into a Pydantic model.

    Multi-level fail-safe matching the pattern in tasks.py.
    """
    json_match = re.search(r"\{.*\}", response, re.DOTALL)
    if not json_match:
        log.debug("No JSON object in response: %s", response[:200])
        return None

    try:
        raw = json.loads(json_match.group())
    except json.JSONDecodeError:
        log.debug("Invalid JSON: %s", json_match.group()[:200])
        return None

    try:
        return model_cls.model_validate(raw)
    except Exception as e:
        log.debug("Pydantic validation failed for %s: %s", model_cls.__name__, e)
        return None


def _parse_json_array(response: str) -> list[dict]:
    """Parse a JSON array from an LLM response."""
    match = re.search(r"\[.*\]", response, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []
