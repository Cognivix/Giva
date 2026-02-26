"""Task review & classification pipeline.

Runs after task extraction to:
1. Sanity-check tasks (code-level: expired, answered, past events)
2. Merge semantic duplicates (filter model)
3. Classify tasks by actionability (assistant model) — with dismissal learning
4. Route classified tasks: queue autonomous agents, enrich context, upgrade projects

Triggered by the sync scheduler in high-performance mode, deferred under
power constraints.  Processes all unclassified pending tasks, not just
newly extracted ones.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from giva.config import GivaConfig
from giva.db.models import Goal, Task
from giva.db.store import Store

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts (inline, following daily_review.py convention)
# ---------------------------------------------------------------------------

DEDUP_PROMPT = """Identify groups of semantically duplicate tasks in the list below.
Two tasks are duplicates if they describe the same real-world action, even if worded
differently (e.g. "Reply to Sarah's email" and "Respond to Sarah about Q4 budget").

Tasks:
{tasks_block}

Rules:
- Only group tasks that are genuinely about the same action.
- For each group, pick the task with the clearest title as canonical_id.
- If an improved title is warranted, provide merged_title; otherwise set it to null.
- If both tasks have complementary descriptions, combine them into merged_description.
- Tasks that have no duplicates should NOT appear in any group.

Respond with ONLY a JSON object:
{{"groups": [
  {{"canonical_id": N, "duplicate_ids": [M, ...], \
"merged_title": "improved title or null", \
"merged_description": "combined description or null"}}
]}} /no_think"""

CLASSIFY_PROMPT = """Classify each task by how it should be handled.

User profile:
{profile_summary}

Active goals:
{goals_summary}

Available agents (for autonomous execution):
{agent_catalog}

{review_memory}

{dismissal_history}

Tasks to classify:
{tasks_block}

Categories:
- "autonomous": Easy, obvious tasks that an agent can prepare without user input. \
Examples: research a topic, draft a clearly-scoped email reply (e.g. politely decline), \
search for information. The agent prepares results; user confirms before external actions.
- "needs_input": Tasks that COULD be automated but need a user decision first. \
Examples: draft response where the approach isn't obvious, decide between multiple options.
- "user_only": Tasks only the user can personally do (call someone, attend a meeting, \
make a personal decision). We remind and provide context.
- "project": Tasks that are really complex, multi-step projects that should be upgraded \
to mid-term goals with strategy brainstorming.
- "dismiss": Tasks that should be dismissed because they are unnecessary, redundant, \
or the user has consistently ignored similar tasks in the past. \
Examples: trivial meeting prep for internal low-stakes meetings (the calendar is enough), \
tasks whose deadlines have already passed, tasks about emails that have already been answered. \
Consider the user's dismissal patterns above — if the user consistently dismisses a type of \
task, that's a strong signal to dismiss similar ones. But evaluate each task individually; \
context matters.

Rules:
- When in doubt, prefer "needs_input" over "autonomous" (safer).
- For "autonomous" tasks, suggest the best agent_id from the available agents list.
- For "needs_input" / "user_only", provide enrichment_query: search terms to find \
relevant emails/events/notes for context.
- For "project" tasks, provide goal_title (concise) and goal_tier ("mid_term" or \
"short_term").
- For "dismiss", explain why (reasoning field). This is important for learning.
- If you notice recurring patterns in what should be dismissed, describe them in \
the review_observations field at the top level.

Respond with ONLY a JSON object:
{{"review_observations": "optional: any patterns you noticed about the user's tasks or \
preferences, or null",
"tasks": [
  {{"task_id": N, "classification": "autonomous|needs_input|user_only|project|dismiss", \
"reasoning": "brief reason", "suggested_agent": "agent_id or null", \
"enrichment_query": "search terms or null", \
"goal_title": "goal title or null", "goal_tier": "mid_term"}}
]}} /no_think"""

ENRICH_PROMPT = """Enrich this task description with the relevant context provided.

Task: {task_title}
Current description: {task_description}
Classification: {classification}

Related context:
{context_block}

Write an enriched description that includes:
- The original task intent
- Key relevant details from the context (names, dates, specifics)
- For "needs_input": what decision the user needs to make and what the options are
- For "user_only": when/where to do it and any preparation needed

Keep it concise (2-4 paragraphs). Use markdown formatting.

Respond with ONLY a JSON object:
{{"enriched_description": "the enriched description"}} /no_think"""


# ---------------------------------------------------------------------------
# JSON parsing (reuses daily_review.py pattern)
# ---------------------------------------------------------------------------

def _parse_json_response(raw: str) -> Optional[dict]:
    """Parse JSON from LLM output with multi-level fallback."""
    raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)

    # Direct parse
    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Markdown code block
    md_match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if md_match:
        try:
            parsed = json.loads(md_match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # First JSON object
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    log.debug("Failed to parse task review JSON: %s", raw[:200])
    return None


# ---------------------------------------------------------------------------
# Pre-classification sanity checks (code-level, no LLM)
# ---------------------------------------------------------------------------


def _is_expired_deadline(task: Task) -> bool:
    """True if the task has a due date that has already passed."""
    if not task.due_date:
        return False
    return task.due_date < datetime.now()


def _is_answered_email(task: Task, store: Store) -> bool:
    """True if the source email has been replied to (thread has a newer message)."""
    if task.source_type != "email" or not task.source_id:
        return False
    email = store.get_email_by_id(task.source_id)
    if not email:
        return False
    # Check if any email in the DB references this email (i.e. is a reply to it)
    if email.message_id:
        try:
            with store._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM emails WHERE in_reply_to = ?",
                    (email.message_id,),
                ).fetchone()
                if row and row["c"] > 0:
                    return True
        except Exception:
            pass
    return False


def _is_past_event(task: Task, store: Store) -> bool:
    """True if the source event has already occurred."""
    if task.source_type != "event" or not task.source_id:
        return False
    event = store.get_event_by_id(task.source_id)
    if not event:
        return False
    return event.dtstart < datetime.now()


def _sanity_check_tasks(
    tasks: list[Task],
    store: Store,
    broadcast_fn: Optional[Callable] = None,
) -> list[Task]:
    """Run code-level sanity checks and auto-dismiss obviously stale tasks.

    Returns the list of tasks that passed all checks (not dismissed).
    """
    surviving = []
    for task in tasks:
        reason = None
        if _is_expired_deadline(task):
            reason = "expired_deadline"
        elif _is_answered_email(task, store):
            reason = "answered_email"
        elif _is_past_event(task, store):
            reason = "past_event"

        if reason:
            store.update_task(task.id, classification="dismiss")
            store.update_task_status(task.id, "dismissed")
            log.info(
                "Sanity check dismissed task #%d (%s): %s",
                task.id, reason, task.title,
            )
            if broadcast_fn:
                broadcast_fn({
                    "event": "task_sanity_dismissed",
                    "data": json.dumps({
                        "task_id": task.id,
                        "title": task.title,
                        "reason": reason,
                    }),
                })
        else:
            surviving.append(task)

    return surviving


# ---------------------------------------------------------------------------
# Review memory: dismissal patterns + LLM observations
# ---------------------------------------------------------------------------


def _get_dismissal_history(store: Store, limit: int = 30) -> str:
    """Build a summary of recently dismissed tasks for the classify prompt."""
    try:
        dismissed = store.get_tasks(status="dismissed", limit=limit)
        if not dismissed:
            return ""
        lines = ["Recently dismissed tasks (by user or system):"]
        for t in dismissed[:20]:
            src = f"source={t.source_type}"
            cls = f", classification={t.classification}" if t.classification else ""
            lines.append(f"- \"{t.title}\" ({src}{cls})")
        return "\n".join(lines)
    except Exception:
        return ""


def _get_review_memory(store: Store) -> str:
    """Retrieve cached review patterns from profile_data."""
    try:
        profile = store.get_profile()
        if not profile:
            return ""
        patterns = profile.profile_data.get("task_review_patterns", {})
        if not patterns:
            return ""
        lines = ["Review memory (patterns recognized from past reviews):"]
        for obs in patterns.get("observations", []):
            lines.append(f"- {obs}")
        suppressed = patterns.get("suppressed_types", [])
        if suppressed:
            lines.append("Suppressed task types (user consistently dismisses these):")
            for s in suppressed:
                lines.append(f"  - {s}")
        return "\n".join(lines)
    except Exception:
        return ""


def _learn_dismissal_patterns(store: Store) -> None:
    """Analyze dismissed tasks and update review patterns in profile_data.

    Looks for task types/keywords that are consistently dismissed and
    records them for future review cycles.
    """
    try:
        dismissed = store.get_tasks(status="dismissed", limit=100)
        if len(dismissed) < 3:
            return

        # Count source_type + keyword patterns
        type_counts: dict[str, int] = {}
        for t in dismissed:
            key = t.source_type
            type_counts[key] = type_counts.get(key, 0) + 1

        # Identify consistently dismissed source types (>= 5 occurrences)
        suppressed = []
        for src_type, count in type_counts.items():
            if count >= 5:
                suppressed.append(
                    f"{src_type} tasks (dismissed {count} times)"
                )

        # Keyword frequency in dismissed task titles
        word_counts: dict[str, int] = {}
        for t in dismissed:
            words = set(
                w.lower()
                for w in re.split(r"\W+", t.title)
                if len(w) > 3
            )
            for w in words:
                word_counts[w] = word_counts.get(w, 0) + 1

        # Top keywords that appear in many dismissed tasks
        frequent_keywords = [
            f"\"{word}\" (in {count} dismissed tasks)"
            for word, count in sorted(word_counts.items(), key=lambda x: -x[1])[:5]
            if count >= 3
        ]
        if frequent_keywords:
            suppressed.append(
                "Common keywords in dismissed tasks: " + ", ".join(frequent_keywords)
            )

        # Update profile_data
        profile = store.get_profile()
        if not profile:
            return

        patterns = profile.profile_data.get("task_review_patterns", {})
        patterns["suppressed_types"] = suppressed
        patterns["last_learned"] = datetime.now().isoformat()
        store.update_profile_data({"task_review_patterns": patterns})
        log.info("Updated dismissal patterns: %d suppressed types", len(suppressed))

    except Exception as e:
        log.debug("Dismissal pattern learning error: %s", e)


def _save_review_observations(observations: Optional[str], store: Store) -> None:
    """Persist LLM review observations into profile_data."""
    if not observations:
        return
    try:
        profile = store.get_profile()
        if not profile:
            return
        patterns = profile.profile_data.get("task_review_patterns", {})
        obs_list = patterns.get("observations", [])
        # Keep last 10 observations, deduplicate
        if observations not in obs_list:
            obs_list.append(observations)
            obs_list = obs_list[-10:]
        patterns["observations"] = obs_list
        store.update_profile_data({"task_review_patterns": patterns})
    except Exception as e:
        log.debug("Save review observations error: %s", e)


# ---------------------------------------------------------------------------
# Step 1: Duplicate detection
# ---------------------------------------------------------------------------

def _format_tasks_for_prompt(tasks: list[Task], max_desc_len: int = 100) -> str:
    """Format tasks into a compact block for LLM prompts."""
    lines = []
    for t in tasks:
        desc = (t.description[:max_desc_len] + "...") if len(t.description) > max_desc_len else t.description
        goal_note = f", goal_id={t.goal_id}" if t.goal_id else ""
        lines.append(
            f"- #{t.id}: \"{t.title}\" (priority={t.priority}{goal_note})"
            f"\n  description: {desc or 'none'}"
        )
    return "\n".join(lines)


def _detect_duplicates(
    tasks: list[Task],
    config: GivaConfig,
) -> list[dict]:
    """Use the filter model to find semantic duplicate groups.

    Returns a list of group dicts: {canonical_id, duplicate_ids, merged_title,
    merged_description}.
    """
    from giva.llm.engine import manager

    if len(tasks) < 2:
        return []

    tasks_block = _format_tasks_for_prompt(tasks)
    prompt = DEDUP_PROMPT.format(tasks_block=tasks_block)

    try:
        raw = manager.generate(
            config.llm.filter_model,
            [{"role": "user", "content": prompt}],
            max_tokens=512,
            temp=0.1,
            top_p=0.9,
        )
        result = _parse_json_response(raw)
        if not result:
            return []

        # Validate task IDs against actual task set
        valid_ids = {t.id for t in tasks}
        groups = []
        for g in result.get("groups", []):
            canonical = g.get("canonical_id")
            dupes = g.get("duplicate_ids", [])
            if canonical not in valid_ids:
                continue
            dupes = [d for d in dupes if d in valid_ids and d != canonical]
            if not dupes:
                continue
            groups.append({
                "canonical_id": canonical,
                "duplicate_ids": dupes,
                "merged_title": g.get("merged_title"),
                "merged_description": g.get("merged_description"),
            })

        return groups

    except Exception as e:
        log.debug("Duplicate detection error: %s", e)
        return []


def _execute_merges(
    groups: list[dict],
    store: Store,
    broadcast_fn: Optional[Callable] = None,
) -> int:
    """Merge duplicate groups: update canonical, dismiss duplicates.

    Returns the number of tasks dismissed.
    """
    dismissed = 0
    id_to_task = {}

    for group in groups:
        all_ids = [group["canonical_id"]] + group["duplicate_ids"]
        for tid in all_ids:
            if tid not in id_to_task:
                task = store.get_task(tid)
                if task:
                    id_to_task[tid] = task

        canonical = id_to_task.get(group["canonical_id"])
        if not canonical:
            continue

        # Update canonical task with merged info
        updates = {}
        if group.get("merged_title"):
            updates["title"] = group["merged_title"]
        if group.get("merged_description"):
            updates["description"] = group["merged_description"]

        # Take highest priority and earliest due date from all tasks in the group
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        best_priority = canonical.priority
        best_due = canonical.due_date
        best_goal_id = canonical.goal_id

        for dup_id in group["duplicate_ids"]:
            dup = id_to_task.get(dup_id)
            if not dup:
                continue
            if priority_rank.get(dup.priority, 2) < priority_rank.get(best_priority, 2):
                best_priority = dup.priority
            if dup.due_date and (not best_due or dup.due_date < best_due):
                best_due = dup.due_date
            if dup.goal_id and not best_goal_id:
                best_goal_id = dup.goal_id

        updates["priority"] = best_priority
        if best_due:
            updates["due_date"] = best_due
        if best_goal_id:
            updates["goal_id"] = best_goal_id

        if updates:
            store.update_task(canonical.id, **updates)

        # Dismiss duplicates
        for dup_id in group["duplicate_ids"]:
            store.update_task_status(dup_id, "dismissed")
            dismissed += 1
            log.info(
                "Merged task #%d into #%d (dismissed as duplicate)",
                dup_id, canonical.id,
            )

        if broadcast_fn:
            broadcast_fn({
                "event": "tasks_merged",
                "data": json.dumps({
                    "canonical_id": canonical.id,
                    "canonical_title": group.get("merged_title") or canonical.title,
                    "dismissed_ids": group["duplicate_ids"],
                }),
            })

    return dismissed


# ---------------------------------------------------------------------------
# Step 2: Classification
# ---------------------------------------------------------------------------

def _classify_tasks(
    tasks: list[Task],
    store: Store,
    config: GivaConfig,
) -> tuple[list[dict], Optional[str]]:
    """Classify tasks using the assistant model.

    Returns (classifications, review_observations) where observations
    are optional LLM-generated patterns to cache for future reviews.
    """
    from giva.intelligence.goals import get_goals_summary
    from giva.intelligence.profile import get_profile_summary
    from giva.llm.engine import manager

    profile_summary = get_profile_summary(store) or "No profile available."
    goals_summary = get_goals_summary(store) or "No active goals."

    # Build agent catalog
    try:
        from giva.agents.registry import registry
        agent_catalog = registry.catalog_text()
    except Exception:
        agent_catalog = "No agents available."

    # Build review memory and dismissal history for the LLM
    review_memory = _get_review_memory(store)
    dismissal_history = _get_dismissal_history(store)

    tasks_block = _format_tasks_for_prompt(tasks, max_desc_len=200)

    prompt = CLASSIFY_PROMPT.format(
        profile_summary=profile_summary,
        goals_summary=goals_summary,
        agent_catalog=agent_catalog,
        review_memory=review_memory or "No review memory yet.",
        dismissal_history=dismissal_history or "No dismissal history yet.",
        tasks_block=tasks_block,
    )

    try:
        raw = manager.generate(
            config.llm.model,
            [{"role": "user", "content": prompt}],
            max_tokens=1024,
            temp=0.2,
            top_p=0.9,
        )
        result = _parse_json_response(raw)
        if not result:
            return [], None

        # Extract LLM observations for caching
        observations = result.get("review_observations")

        valid_ids = {t.id for t in tasks}
        valid_classes = {"autonomous", "needs_input", "user_only", "project", "dismiss"}
        classifications = []
        for item in result.get("tasks", []):
            task_id = item.get("task_id")
            cls = item.get("classification", "needs_input")
            if task_id not in valid_ids:
                continue
            if cls not in valid_classes:
                cls = "needs_input"  # Safe default
            classifications.append({
                "task_id": task_id,
                "classification": cls,
                "reasoning": item.get("reasoning", ""),
                "suggested_agent": item.get("suggested_agent"),
                "enrichment_query": item.get("enrichment_query"),
                "goal_title": item.get("goal_title"),
                "goal_tier": item.get("goal_tier", "mid_term"),
            })

        return classifications, observations

    except Exception as e:
        log.debug("Task classification error: %s", e)
        return [], None


# ---------------------------------------------------------------------------
# Step 3: Action routing
# ---------------------------------------------------------------------------

def _route_autonomous(
    task: Task,
    classification: dict,
    agent_queue: Any,
    broadcast_fn: Optional[Callable] = None,
) -> Optional[dict]:
    """Queue an autonomous task for agent execution with user confirmation."""
    if agent_queue is None:
        return None

    try:
        from giva.agents.queue import AgentJob

        agent_id = classification.get("suggested_agent") or "orchestrator"
        job = AgentJob(
            job_id=str(uuid.uuid4()),
            agent_id=agent_id,
            query=f"Complete this task: {task.title}. {task.description}",
            context={"task_id": task.id, "source": "task_review"},
            priority=1,  # Between user (0) and scheduler (2)
            status="pending_confirmation",
            source="task",
            task_id=task.id,
            goal_id=task.goal_id,
            plan_summary=(
                f"Auto-review classified this as an autonomous task: "
                f"{task.title}. {classification.get('reasoning', '')}"
            ),
        )
        agent_queue.enqueue(job)
        log.info(
            "Autonomous task #%d queued for agent %s (job %s)",
            task.id, agent_id, job.job_id[:8],
        )
        return {
            "type": "task_auto_queued",
            "task_id": task.id,
            "agent_id": agent_id,
            "job_id": job.job_id,
        }
    except Exception as e:
        log.debug("Failed to queue autonomous task #%d: %s", task.id, e)
        return None


def _route_enrich(
    task: Task,
    classification: dict,
    store: Store,
    config: GivaConfig,
) -> Optional[dict]:
    """Enrich a needs_input or user_only task with relevant context."""
    from giva.llm.engine import manager

    enrichment_query = classification.get("enrichment_query")
    context_parts = []

    # FTS search for related emails
    if enrichment_query:
        try:
            emails = store.search_emails(enrichment_query, limit=3)
            for e in emails:
                context_parts.append(
                    f"Email from {e.from_name or e.from_addr} ({e.date_sent.strftime('%b %d')}): "
                    f"{e.subject}\n{(e.body_plain or '')[:300]}"
                )
        except Exception:
            pass

    # Related upcoming events
    try:
        events = store.get_upcoming_events(days=7)
        for ev in events[:3]:
            context_parts.append(
                f"Event: {ev.summary} on {ev.dtstart.strftime('%b %d %H:%M')}"
                f" — {ev.description[:200] if ev.description else 'no details'}"
            )
    except Exception:
        pass

    # Goal context if linked
    if task.goal_id:
        goal = store.get_goal(task.goal_id)
        if goal:
            context_parts.append(
                f"Linked goal: {goal.title} ({goal.tier}) — {goal.description[:200]}"
            )

    if not context_parts:
        return None

    context_block = "\n\n".join(context_parts)
    prompt = ENRICH_PROMPT.format(
        task_title=task.title,
        task_description=task.description or "No description",
        classification=classification["classification"],
        context_block=context_block,
    )

    try:
        raw = manager.generate(
            config.llm.filter_model,
            [{"role": "user", "content": prompt}],
            max_tokens=512,
            temp=0.2,
            top_p=0.9,
        )
        result = _parse_json_response(raw)
        if result and result.get("enriched_description"):
            store.update_task(task.id, description=result["enriched_description"])
            log.info(
                "Enriched task #%d (%s, %d chars)",
                task.id, classification["classification"],
                len(result["enriched_description"]),
            )
            return {
                "type": "task_enriched",
                "task_id": task.id,
                "classification": classification["classification"],
            }
    except Exception as e:
        log.debug("Task enrichment error for #%d: %s", task.id, e)

    return None


def _route_dismiss(
    task: Task,
    classification: dict,
    store: Store,
    broadcast_fn: Optional[Callable] = None,
) -> Optional[dict]:
    """Dismiss a task that the LLM determined is unnecessary."""
    store.update_task_status(task.id, "dismissed")
    log.info(
        "LLM dismissed task #%d: %s (reason: %s)",
        task.id, task.title, classification.get("reasoning", ""),
    )
    if broadcast_fn:
        broadcast_fn({
            "event": "task_dismissed",
            "data": json.dumps({
                "task_id": task.id,
                "title": task.title,
                "reasoning": classification.get("reasoning", ""),
            }),
        })
    return {
        "type": "task_dismissed",
        "task_id": task.id,
        "reasoning": classification.get("reasoning", ""),
    }


def _route_project(
    task: Task,
    classification: dict,
    store: Store,
    broadcast_fn: Optional[Callable] = None,
) -> Optional[dict]:
    """Upgrade a project-class task to a mid-term goal."""
    goal_title = classification.get("goal_title") or task.title
    goal_tier = classification.get("goal_tier", "mid_term")
    if goal_tier not in ("long_term", "mid_term", "short_term"):
        goal_tier = "mid_term"

    goal = Goal(
        title=goal_title,
        tier=goal_tier,
        description=task.description or "",
        priority=task.priority,
    )
    goal_id = store.add_goal(goal)

    # Dismiss the original task (no longer actionable as a task)
    store.update_task_status(task.id, "dismissed")

    log.info(
        "Task #%d upgraded to goal #%d: %s (%s)",
        task.id, goal_id, goal_title, goal_tier,
    )

    if broadcast_fn:
        broadcast_fn({
            "event": "task_upgraded_to_goal",
            "data": json.dumps({
                "task_id": task.id,
                "task_title": task.title,
                "goal_id": goal_id,
                "goal_title": goal_title,
                "goal_tier": goal_tier,
            }),
        })

    return {
        "type": "task_upgraded_to_goal",
        "task_id": task.id,
        "goal_id": goal_id,
        "goal_title": goal_title,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def review_pending_tasks(
    store: Store,
    config: GivaConfig,
    agent_queue: Any = None,
    broadcast_fn: Optional[Callable] = None,
) -> int:
    """Run the full task review pipeline: sanity → dedup → classify → route → learn.

    Returns the number of tasks classified. Caller must hold _llm_lock.
    """
    if not config.task_review.enabled:
        return 0

    # Step 1: Load unclassified tasks
    tasks = store.get_unclassified_tasks(limit=config.task_review.batch_size)
    if not tasks:
        return 0

    log.info("Task review: %d unclassified tasks to process", len(tasks))

    # Step 2: Sanity checks (code-level, no LLM)
    tasks = _sanity_check_tasks(tasks, store, broadcast_fn)
    if not tasks:
        return 0

    # Step 3: Deduplicate (filter model)
    dedup_batch = tasks[:config.task_review.dedup_batch_size]
    if len(dedup_batch) >= 2:
        groups = _detect_duplicates(dedup_batch, config)
        if groups:
            dismissed = _execute_merges(groups, store, broadcast_fn)
            log.info("Task review dedup: %d groups, %d tasks dismissed", len(groups), dismissed)

    # Step 4: Reload after dedup, then classify (assistant model)
    tasks = store.get_unclassified_tasks(limit=config.task_review.classify_batch_size)
    if not tasks:
        return 0

    classifications, observations = _classify_tasks(tasks, store, config)
    if not classifications:
        return 0

    # Save LLM observations for future review cycles
    _save_review_observations(observations, store)

    # Build lookup
    id_to_task = {t.id: t for t in tasks}
    actions = []
    classified_count = 0

    # Step 5: Store classifications and route
    for cls in classifications:
        task_id = cls["task_id"]
        task = id_to_task.get(task_id)
        if not task:
            continue

        # Persist classification
        store.update_task(task_id, classification=cls["classification"])
        classified_count += 1

        category = cls["classification"]

        if category == "autonomous":
            action = _route_autonomous(task, cls, agent_queue, broadcast_fn)
            if action:
                actions.append(action)

        elif category in ("needs_input", "user_only"):
            action = _route_enrich(task, cls, store, config)
            if action:
                actions.append(action)

        elif category == "project":
            action = _route_project(task, cls, store, broadcast_fn)
            if action:
                actions.append(action)

        elif category == "dismiss":
            action = _route_dismiss(task, cls, store, broadcast_fn)
            if action:
                actions.append(action)

    # Step 6: Learn from dismissal patterns (update profile_data)
    _learn_dismissal_patterns(store)

    # Broadcast completion summary
    if broadcast_fn and classified_count > 0:
        counts = {}
        for cls in classifications:
            cat = cls["classification"]
            counts[cat] = counts.get(cat, 0) + 1
        broadcast_fn({
            "event": "task_review_complete",
            "data": json.dumps({
                "classified": classified_count,
                "counts": counts,
                "actions": len(actions),
            }),
        })

    counts = {}
    for c in classifications:
        cat = c["classification"]
        counts[cat] = counts.get(cat, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    log.info(
        "Task review complete: %d classified (%s), %d actions",
        classified_count, summary, len(actions),
    )

    return classified_count
