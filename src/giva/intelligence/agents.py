"""Post-chat agent pipeline: intent detection, conversation tagging, and action routing.

Runs after every chat response using the filter model in a single combined call.
Detects intents (create task, create objective, log progress, save fact) and routes actions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from giva.config import GivaConfig
from giva.db.models import Goal, Task
from giva.db.store import Store

log = logging.getLogger(__name__)

# --- Prompt for combined post-chat agent ---

POST_CHAT_PROMPT = """Analyze this conversation turn between a user and their personal assistant.

User: {query}
Assistant: {response}

Active goals:
{goals_list}

Pending tasks (titles):
{tasks_list}

{goal_context}

Detect the following from this exchange:

1. **Intents**: Did the user ask to create a task, create an objective/sub-goal, \
log progress, draft an email, or share a preference?
   - Use "create_task" for specific actionable items (things to do).
   - Use "create_objective" for broader sub-goals or milestones under a parent goal.
2. **Topic**: What is the main topic of this exchange? (1-3 words)
3. **Progress**: Does this exchange indicate progress toward any of the active goals \
listed above?

Respond with ONLY a JSON object:
{{
  "intents": [
    {{
      "type": "create_task" | "create_objective" | "complete_task" | "progress" \
| "preference" | "none",
      "title": "task or objective title or null",
      "description": "brief description or null",
      "priority": "high" | "medium" | "low" | null,
      "tier": "mid_term" | "short_term" | null,
      "goal_id": null,
      "detail": "relevant detail"
    }}
  ],
  "topic": "string",
  "progress": [
    {{"goal_id": 1, "note": "brief progress description"}}
  ]
}} /no_think"""


def run_post_chat_agent(
    query: str,
    response: str,
    store: Store,
    config: GivaConfig,
    goal_id: Optional[int] = None,
) -> list[dict]:
    """Run the combined post-chat agent after a chat response.

    Uses the filter model for a single LLM call that:
    1. Detects intents (task creation, objective creation, progress, preferences)
    2. Tags the conversation topic
    3. Detects goal progress

    Args:
        goal_id: When set, auto-links created tasks/objectives to this goal
            and injects goal context into the prompt.

    Returns a list of action dicts for SSE broadcasting.
    """
    from giva.llm.engine import manager

    actions = []

    # Build context for the agent
    goals = store.get_goals(status="active")
    goals_list = (
        "\n".join(f"- ID {g.id}: {g.title} ({g.tier}, {g.category})" for g in goals)
        if goals else "No active goals."
    )

    tasks = store.get_tasks(status="pending", limit=10)
    tasks_list = (
        "\n".join(f"- #{t.id}: {t.title}" for t in tasks)
        if tasks else "No pending tasks."
    )

    # Build goal context when in goal chat
    goal_context = ""
    if goal_id:
        goal = store.get_goal(goal_id)
        if goal:
            goal_context = (
                f"Current goal context: discussing '{goal.title}' "
                f"({goal.tier}, {goal.category}). "
                f"Auto-link any new tasks/objectives to goal_id={goal_id}."
            )

    prompt = POST_CHAT_PROMPT.format(
        query=query[:500],
        response=response[:500],
        goals_list=goals_list,
        tasks_list=tasks_list,
        goal_context=goal_context,
    )

    messages = [{"role": "user", "content": prompt}]

    try:
        raw = manager.generate(
            config.llm.filter_model,
            messages,
            max_tokens=512,
            temp=0.1,
            top_p=0.9,
        )
        result = _parse_agent_response(raw)
        if result is None:
            return actions

        # Process intents
        for intent in result.get("intents", []):
            intent_type = intent.get("type", "none")

            if intent_type == "create_task":
                action = _handle_create_task(intent, store, goal_id=goal_id)
                if action:
                    actions.append(action)

            elif intent_type == "create_objective":
                action = _handle_create_objective(intent, store, goal_id=goal_id)
                if action:
                    actions.append(action)

            elif intent_type == "complete_task":
                action = _handle_complete_task(intent, store, tasks)
                if action:
                    actions.append(action)

            elif intent_type == "preference":
                action = _handle_preference(intent, store)
                if action:
                    actions.append(action)

        # Process goal progress
        for prog in result.get("progress", []):
            prog_goal_id = prog.get("goal_id")
            note = prog.get("note", "")
            if prog_goal_id and note and store.get_goal(prog_goal_id):
                store.add_goal_progress(prog_goal_id, note, "chat")
                actions.append({
                    "type": "goal_progress",
                    "goal_id": prog_goal_id,
                    "note": note,
                })

    except Exception as e:
        log.debug("Post-chat agent error: %s", e)

    return actions


def _handle_create_task(
    intent: dict, store: Store, goal_id: Optional[int] = None
) -> Optional[dict]:
    """Create a task from a detected intent, auto-linking to a goal if possible."""
    title = intent.get("title")
    if not title:
        return None

    # Check for duplicates (simple title match)
    existing = store.get_tasks(status="pending", limit=50)
    for t in existing:
        if t.title.lower() == title.lower():
            log.debug("Skipping duplicate task: %s", title)
            return None

    # Use explicit goal_id from context if provided, then intent, then heuristic
    intent_goal_id = intent.get("goal_id") or goal_id
    if not intent_goal_id:
        intent_goal_id = _auto_link_goal(title, intent.get("description", ""), store)

    task = Task(
        title=title,
        description=intent.get("description", "") or "",
        source_type="chat",
        source_id=0,
        priority=intent.get("priority") or "medium",
        goal_id=intent_goal_id,
    )
    task_id = store.add_task(task)
    log.info(
        "Post-chat agent created task #%d: %s (goal=%s)", task_id, title, intent_goal_id
    )

    return {
        "type": "task_created",
        "task_id": task_id,
        "title": title,
        "priority": task.priority,
        "goal_id": intent_goal_id,
    }


def _handle_create_objective(
    intent: dict, store: Store, goal_id: Optional[int] = None
) -> Optional[dict]:
    """Create a child goal (objective) from a detected intent."""
    title = intent.get("title")
    if not title:
        return None

    parent_id = goal_id

    # Infer child tier from the parent's tier
    tier = intent.get("tier") or "mid_term"
    if parent_id:
        parent = store.get_goal(parent_id)
        if parent:
            if parent.tier == "long_term":
                tier = "mid_term"
            elif parent.tier == "mid_term":
                tier = "short_term"
            else:
                # short_term goals shouldn't spawn children in the hierarchy
                parent_id = None

    # Check for duplicates (simple title match among children)
    if parent_id:
        children = store.get_child_goals(parent_id)
        for c in children:
            if c.title.lower() == title.lower():
                log.debug("Skipping duplicate objective: %s", title)
                return None

    goal = Goal(
        title=title,
        tier=tier,
        description=intent.get("description", "") or "",
        parent_id=parent_id,
        priority=intent.get("priority") or "medium",
    )
    new_goal_id = store.add_goal(goal)
    log.info(
        "Post-chat agent created objective #%d: %s (parent=%s, tier=%s)",
        new_goal_id, title, parent_id, tier,
    )

    return {
        "type": "objective_created",
        "goal_id": new_goal_id,
        "title": title,
        "tier": tier,
        "parent_id": parent_id,
    }


def _auto_link_goal(
    title: str, description: str, store: Store
) -> Optional[int]:
    """Try to match a task to an active goal using keyword overlap.

    Uses simple word overlap scoring — no LLM call — to keep post-chat
    agent latency low.  Returns the best-matching goal_id or None.
    """
    goals = store.get_goals(status="active")
    if not goals:
        return None

    # Build task words from title + description
    task_words = set(
        w.lower()
        for w in re.split(r"\W+", f"{title} {description}")
        if len(w) > 2
    )
    if not task_words:
        return None

    best_id = None
    best_score = 0

    for g in goals:
        goal_words = set(
            w.lower()
            for w in re.split(r"\W+", f"{g.title} {g.category or ''}")
            if len(w) > 2
        )
        overlap = len(task_words & goal_words)
        if overlap > best_score:
            best_score = overlap
            best_id = g.id

    # Require at least 1 meaningful word overlap
    return best_id if best_score >= 1 else None


def _handle_complete_task(
    intent: dict, store: Store, pending_tasks: list
) -> Optional[dict]:
    """Mark a task as done if the user indicated completion."""
    title = intent.get("title", "").lower()
    if not title:
        return None

    # Find best match by title
    for t in pending_tasks:
        if title in t.title.lower() or t.title.lower() in title:
            store.update_task_status(t.id, "done")
            log.info("Post-chat agent completed task #%d: %s", t.id, t.title)
            return {
                "type": "task_completed",
                "task_id": t.id,
                "title": t.title,
            }

    return None


def _handle_preference(intent: dict, store: Store) -> Optional[dict]:
    """Save a user preference as a learned fact."""
    detail = intent.get("detail", "")
    if not detail:
        return None

    # Store as learned fact in profile_data
    profile = store.get_profile()
    if profile:
        facts = profile.profile_data.get("learned_facts", [])
        # Avoid duplicates
        if detail not in facts:
            facts.append(detail)
            store.update_profile_data({"learned_facts": facts})
            log.info("Post-chat agent saved preference: %s", detail[:80])
            return {
                "type": "preference_saved",
                "detail": detail,
            }

    return None


def _parse_agent_response(raw: str) -> Optional[dict]:
    """Parse the combined post-chat agent JSON response."""
    # Strip think tags if present
    raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)

    # Try direct parse
    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Extract JSON from markdown code block
    md_match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if md_match:
        try:
            parsed = json.loads(md_match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Extract first JSON object
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    log.debug("Failed to parse post-chat agent response: %s", raw[:200])
    return None
