"""Daily review intelligence: scheduled reviews, background strategy, plan status checks.

Provides functions to:
- Check if a daily review is due
- Generate and stream a daily review prompt
- Save review responses and extract goal progress
- Run background strategy generation for goals without strategies
- Review tactical plan status for stalled objectives
- Extract durable facts from session summaries (Fact Extractor)
- Detect stale/overdue tasks before daily review (Stale Task Detector)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Generator, Optional

from giva.config import GivaConfig
from giva.db.models import DailyReview
from giva.db.store import Store
from giva.intelligence.goals import (
    _parse_json_response,
    get_goals_summary,
)
from giva.llm.structured import DailyReviewResult

log = logging.getLogger(__name__)


def is_review_due(store: Store, config: GivaConfig) -> bool:
    """Return True if no review exists for today AND current hour >= review hour."""
    today = datetime.now().strftime("%Y-%m-%d")
    existing = store.get_daily_review(today)
    if existing:
        return False

    review_hour = getattr(getattr(config, "goals", None), "daily_review_hour", 18)
    return datetime.now().hour >= review_hour


def generate_review(
    store: Store, config: GivaConfig
) -> Generator[str, None, Optional[int]]:
    """Generate a daily review. Yields streamed tokens.

    Creates a DailyReview row before streaming and returns its ID
    via generator return value.
    """
    from giva.intelligence.profile import get_profile_summary
    from giva.llm.engine import manager
    from giva.llm.prompts import (
        DAILY_REVIEW_SYSTEM,
        DAILY_REVIEW_USER,
        format_event_context,
    )

    now = datetime.now()
    now_str = now.strftime("%A, %B %d, %Y")
    today = now.strftime("%Y-%m-%d")

    profile_summary = get_profile_summary(store) or ""
    profile_section = f"User profile:\n{profile_summary}" if profile_summary else ""

    # Active goals with recent progress
    goals_summary = get_goals_summary(store, include_progress=True)
    if not goals_summary:
        goals_summary = "No active goals."

    # Today's completed tasks
    completed = store.get_tasks(status="done", limit=50)
    today_completed = [
        t for t in completed
        if t.created_at and t.created_at.date() == now.date()
    ]
    if today_completed:
        completed_lines = [f"- [{t.priority}] {t.title}" for t in today_completed]
        completed_today = "\n".join(completed_lines)
    else:
        completed_today = "None completed today."

    # Today's events
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    todays_events = store.get_events_range(today_start, today_end)
    events_today = format_event_context(todays_events)

    # Emails sent today (approximate: recent emails from user's address)
    emails_sent_today = "Not tracked."

    # Stale task triage (runs filter model before the review)
    stale_tasks_section = ""
    try:
        stale_results = detect_stale_tasks(store, config)
        if stale_results:
            stale_lines = []
            for s in stale_results:
                stale_lines.append(
                    f"- [{s['action'].upper()}] {s['title']}: {s['reason']}"
                )
            stale_tasks_section = (
                "\n\nStale/overdue tasks flagged for attention:\n"
                + "\n".join(stale_lines)
            )
    except Exception as e:
        log.debug("Stale task detection in review failed: %s", e)

    # Active plans status
    plans_status = _build_plans_status(store)

    # Previous review
    reviews = store.get_recent_reviews(limit=2)
    if reviews:
        # First review in list is most recent; if it's today's, use the next one
        prev = reviews[0] if reviews[0].review_date != today else (reviews[1] if len(reviews) > 1 else None)
        if prev:
            prev_date = prev.review_date
            prev_review = prev.summary or prev.prompt_text[:300]
        else:
            prev_date = "N/A"
            prev_review = "No previous review."
    else:
        prev_date = "N/A"
        prev_review = "No previous review."

    system = DAILY_REVIEW_SYSTEM.format(
        now=now_str,
        profile_section=profile_section,
        goals_summary=goals_summary,
        completed_today=completed_today,
        events_today=events_today,
        emails_sent_today=emails_sent_today,
        plans_status=plans_status + stale_tasks_section,
        prev_date=prev_date,
        prev_review=prev_review,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": DAILY_REVIEW_USER},
    ]

    # Create review row before streaming (prompt_text = system context summary)
    review = DailyReview(
        review_date=today,
        prompt_text=f"Goals: {len(store.get_goals(status='active'))} active. "
                    f"Tasks completed today: {len(today_completed)}. "
                    f"Events today: {len(todays_events)}.",
    )
    try:
        review_id = store.add_daily_review(review)
    except Exception:
        # Review for today already exists (race condition)
        existing = store.get_daily_review(today)
        review_id = existing.id if existing else None

    full_response = []
    for token in manager.stream_generate(
        config.llm.model, messages, max_tokens=1024, temp=0.4, top_p=0.95
    ):
        full_response.append(token)
        yield token

    # Parse review and update
    text = "".join(full_response)
    result = _parse_json_response(text, DailyReviewResult)
    if result and review_id:
        store.update_daily_review(review_id, "", result.summary)
        log.info("Daily review saved (id=%d)", review_id)

    return review_id


def save_review_response(
    review_id: int,
    response: str,
    store: Store,
    config: GivaConfig,
) -> str:
    """Save user's response to a daily review and extract goal progress.

    Returns a summary string.
    """
    from giva.llm.engine import manager

    # Get the review
    reviews = store.get_recent_reviews(limit=5)
    review = None
    for r in reviews:
        if r.id == review_id:
            review = r
            break
    if not review:
        return "Review not found."

    # Use the LLM to summarize the user's response and extract goal updates
    goals = store.get_goals(status="active")
    if not goals:
        store.update_daily_review(review_id, response, response[:200])
        return response[:200]

    goals_list = "\n".join(
        f"- ID {g.id}: {g.title} ({g.tier}, {g.category})" for g in goals
    )

    summary_prompt = (
        f"The user responded to their daily review:\n\n"
        f"User response: {response[:1000]}\n\n"
        f"Active goals:\n{goals_list}\n\n"
        f"Produce a JSON object:\n"
        f'{{"summary": "1-2 sentence summary", '
        f'"goal_updates": [{{"goal_id": N, "progress_note": "brief note"}}]}} /no_think'
    )

    messages = [{"role": "user", "content": summary_prompt}]

    try:
        llm_response = manager.generate(
            config.llm.filter_model, messages, max_tokens=512, temp=0.2, top_p=0.9
        )
        result = _parse_json_response(llm_response, DailyReviewResult)
        if result:
            summary = result.summary
            # Create progress entries from review
            for update in result.goal_updates:
                goal_id = update.get("goal_id")
                note = update.get("progress_note", "")
                if goal_id and note and store.get_goal(goal_id):
                    store.add_goal_progress(goal_id, note, "review")
            store.update_daily_review(review_id, response, summary)
            log.info(
                "Review response saved: %d goal updates", len(result.goal_updates)
            )

            # Run fact extraction (Tier 2 → Tier 3) after review
            try:
                fact_result = extract_facts_from_session(store, config)
                if fact_result.get("new") or fact_result.get("removed"):
                    log.info(
                        "Post-review fact extraction: +%d new, -%d removed",
                        len(fact_result.get("new", [])),
                        len(fact_result.get("removed", [])),
                    )
            except Exception as fe:
                log.debug("Post-review fact extraction error: %s", fe)

            return summary
    except Exception as e:
        log.debug("Review response parsing failed: %s", e)

    # Fallback: save raw response
    store.update_daily_review(review_id, response, response[:200])
    return response[:200]


def run_background_strategy(store: Store, config: GivaConfig) -> int:
    """Background job: generate a strategy for ONE long-term goal without an accepted strategy.

    Returns 1 if a strategy was generated, 0 otherwise.
    """
    goals = store.get_goals(tier="long_term", status="active")
    if not goals:
        return 0

    for goal in goals:
        strategies = store.get_strategies(goal.id, status="accepted")
        if strategies:
            continue  # Already has an accepted strategy

        # Also skip if there's a recent proposed strategy (avoid spamming)
        proposed = store.get_strategies(goal.id, status="proposed")
        if proposed:
            continue

        # Generate strategy for this goal (non-streaming, just consume the generator)
        from giva.intelligence.goals import generate_strategy

        log.info("Background strategy generation for goal %d: %s", goal.id, goal.title)
        try:
            tokens = list(generate_strategy(goal.id, store, config))
            if tokens:
                log.info(
                    "Background strategy generated for goal %d (%d tokens)",
                    goal.id, len(tokens),
                )
                return 1
        except Exception as e:
            log.warning("Background strategy generation failed for goal %d: %s", goal.id, e)

    return 0


def review_tactical_plans(
    store: Store, config: GivaConfig
) -> Generator[str, None, None]:
    """Review status of active tactical plans for mid-term objectives.

    Checks task completion progress, flags stalled objectives.
    Yields streamed assessment text.
    """
    from giva.intelligence.profile import get_profile_summary
    from giva.llm.engine import manager
    from giva.llm.prompts import PLAN_REVIEW_SYSTEM, PLAN_REVIEW_USER

    objectives = store.get_goals(tier="mid_term", status="active")
    if not objectives:
        yield "No active mid-term objectives to review."
        return

    # Build detail for each objective with tasks
    plans_lines = []
    has_tasks = False
    for obj in objectives:
        tasks = store.get_tasks_for_goal(obj.id)
        if not tasks:
            continue
        has_tasks = True

        pending = [t for t in tasks if t.status == "pending"]
        in_progress = [t for t in tasks if t.status == "in_progress"]
        done = [t for t in tasks if t.status == "done"]

        plans_lines.append(f"## {obj.title}")
        plans_lines.append(
            f"Target: {obj.target_date.strftime('%Y-%m-%d') if obj.target_date else 'Not set'}"
        )
        plans_lines.append(
            f"Tasks: {len(done)} done, {len(in_progress)} in progress, {len(pending)} pending"
        )

        # Show overdue tasks
        now = datetime.now()
        for t in pending + in_progress:
            status_marker = "OVERDUE" if t.due_date and t.due_date < now else t.status
            due_str = t.due_date.strftime("%Y-%m-%d") if t.due_date else "no date"
            plans_lines.append(
                f"  - [{t.priority}] {t.title} ({status_marker}, due: {due_str})"
            )

        # Recent progress
        progress = store.get_goal_progress(obj.id, limit=3)
        if progress:
            plans_lines.append("  Recent progress:")
            for p in progress:
                date_str = p.created_at.strftime("%b %d") if p.created_at else "?"
                plans_lines.append(f"    > {date_str} [{p.source}]: {p.note}")
        plans_lines.append("")

    if not has_tasks:
        yield "No active tactical plans (no objectives have linked tasks)."
        return

    plans_detail = "\n".join(plans_lines)

    profile_summary = get_profile_summary(store) or ""
    profile_section = f"User profile:\n{profile_summary}" if profile_summary else ""
    now_str = datetime.now().strftime("%A, %B %d, %Y")

    system = PLAN_REVIEW_SYSTEM.format(
        now=now_str,
        profile_section=profile_section,
        plans_detail=plans_detail,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": PLAN_REVIEW_USER},
    ]

    full_response = []
    for token in manager.stream_generate(
        config.llm.model, messages, max_tokens=1024, temp=0.4, top_p=0.95
    ):
        full_response.append(token)
        yield token

    # Add progress entries for reviewed objectives
    for obj in objectives:
        tasks = store.get_tasks_for_goal(obj.id)
        if tasks:
            done_count = sum(1 for t in tasks if t.status == "done")
            total = len(tasks)
            store.add_goal_progress(
                obj.id,
                f"Plan review: {done_count}/{total} tasks complete.",
                "review",
            )


# --- Fact Extractor (Tier 2 → Tier 3) ---

FACT_EXTRACT_PROMPT = """From today's session summary, extract permanent user facts and preferences.

Only extract DURABLE information — things that will remain true for weeks or months.
Do NOT extract transient info (today's schedule, temporary moods, one-off requests).

Session summary:
{session_summary}

Existing known facts (avoid duplicates):
{existing_facts}

Respond with ONLY a JSON object:
{{"new_facts": ["fact 1", "fact 2"], "obsolete_facts": ["old fact that is no longer true"]}} \
/no_think"""


def extract_facts_from_session(store: Store, config: GivaConfig) -> dict:
    """Extract durable facts from today's session summary into learned_facts.

    Runs the filter model on the session summary (Tier 2) and merges
    new facts into profile_data["learned_facts"] (Tier 3).  Also removes
    obsolete facts flagged by the LLM.

    Returns {"new": [...], "removed": [...]} or empty dict on error/no-op.
    """
    from giva.llm.engine import manager

    profile = store.get_profile()
    if not profile:
        return {}

    session_summary = profile.profile_data.get("session_summary", "")
    if not session_summary or len(session_summary.strip()) < 20:
        log.debug("Fact extractor: no session summary to process")
        return {}

    existing_facts = profile.profile_data.get("learned_facts", [])
    existing_str = (
        "\n".join(f"- {f}" for f in existing_facts)
        if existing_facts else "None yet."
    )

    prompt = FACT_EXTRACT_PROMPT.format(
        session_summary=session_summary[:1500],
        existing_facts=existing_str,
    )

    try:
        raw = manager.generate(
            config.llm.filter_model,
            [{"role": "user", "content": prompt}],
            max_tokens=256,
            temp=0.1,
            top_p=0.9,
        )

        result = _parse_fact_response(raw)
        if result is None:
            return {}

        new_facts = result.get("new_facts", [])
        obsolete = result.get("obsolete_facts", [])

        if not new_facts and not obsolete:
            return {}

        # Merge: add new, remove obsolete
        updated_facts = list(existing_facts)  # copy
        added = []
        for fact in new_facts:
            if isinstance(fact, str) and fact.strip() and fact not in updated_facts:
                updated_facts.append(fact.strip())
                added.append(fact.strip())

        removed = []
        for old in obsolete:
            if isinstance(old, str):
                # Fuzzy match: remove if old is substring of a fact or vice versa
                for existing in list(updated_facts):
                    if old.lower() in existing.lower() or existing.lower() in old.lower():
                        updated_facts.remove(existing)
                        removed.append(existing)
                        break

        if added or removed:
            store.update_profile_data({"learned_facts": updated_facts})
            log.info(
                "Fact extractor: +%d new, -%d obsolete (total: %d)",
                len(added), len(removed), len(updated_facts),
            )

        # Clear session summary after extraction (it's been processed)
        store.update_profile_data({"session_summary": ""})

        return {"new": added, "removed": removed}

    except Exception as e:
        log.debug("Fact extractor error: %s", e)
        return {}


def _parse_fact_response(raw: str) -> Optional[dict]:
    """Parse the fact extractor JSON response with fallback."""
    raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)

    # Try direct parse
    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Extract JSON from markdown or raw
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# --- Stale Task Detector ---

STALE_TASK_PROMPT = """Review these overdue or stale tasks and recommend an action for each.

Current date: {now}

Tasks to review:
{tasks_block}

User's active goals:
{goals_list}

For each task, decide:
- "remind": still relevant, user should be reminded
- "defer": still relevant but low urgency, suggest a new due date
- "retire": no longer relevant or was likely forgotten, suggest dismissing

Respond with ONLY a JSON object:
{{"tasks": [{{"task_id": N, "action": "remind|defer|retire", \
"reason": "brief reason"}}]}} /no_think"""


def detect_stale_tasks(store: Store, config: GivaConfig) -> list[dict]:
    """Detect overdue and orphaned tasks, classify them for the daily review.

    Uses the filter model to triage stale tasks. Returns a list of
    ``{task_id, title, action, reason}`` dicts. Called before daily review
    generation so results can be injected into the review context.
    """
    from giva.llm.engine import manager

    now = datetime.now()

    # Find overdue tasks (have a due_date in the past, still pending/in_progress)
    all_pending = store.get_tasks(status="pending", limit=100)
    all_in_progress = store.get_tasks(status="in_progress", limit=50)
    candidates = all_pending + all_in_progress

    stale = []
    for t in candidates:
        if t.due_date and t.due_date < now:
            stale.append(t)
        elif not t.due_date and t.created_at:
            # Orphan: no due date, older than 7 days
            age_days = (now - t.created_at).days
            if age_days > 7:
                stale.append(t)

    if not stale:
        return []

    # Cap at 15 tasks to keep prompt reasonable
    stale = stale[:15]

    tasks_block_lines = []
    for t in stale:
        due_str = t.due_date.strftime("%Y-%m-%d") if t.due_date else "no due date"
        age = (now - t.created_at).days if t.created_at else "?"
        goal_str = f", goal_id={t.goal_id}" if t.goal_id else ", no goal"
        tasks_block_lines.append(
            f"- #{t.id}: \"{t.title}\" (priority={t.priority}, due={due_str}, "
            f"age={age} days, status={t.status}{goal_str})"
        )

    goals = store.get_goals(status="active")
    goals_list = (
        "\n".join(f"- ID {g.id}: {g.title} ({g.tier})" for g in goals)
        if goals else "No active goals."
    )

    prompt = STALE_TASK_PROMPT.format(
        now=now.strftime("%Y-%m-%d"),
        tasks_block="\n".join(tasks_block_lines),
        goals_list=goals_list,
    )

    try:
        raw = manager.generate(
            config.llm.filter_model,
            [{"role": "user", "content": prompt}],
            max_tokens=512,
            temp=0.1,
            top_p=0.9,
        )

        result = _parse_fact_response(raw)  # reuse same JSON parser
        if result is None:
            return []

        # Build title lookup
        id_to_task = {t.id: t for t in stale}

        output = []
        for item in result.get("tasks", []):
            task_id = item.get("task_id")
            action = item.get("action", "remind")
            reason = item.get("reason", "")
            if task_id and task_id in id_to_task:
                output.append({
                    "task_id": task_id,
                    "title": id_to_task[task_id].title,
                    "action": action,
                    "reason": reason,
                })

        log.info(
            "Stale task detector: %d tasks triaged (%d remind, %d defer, %d retire)",
            len(output),
            sum(1 for o in output if o["action"] == "remind"),
            sum(1 for o in output if o["action"] == "defer"),
            sum(1 for o in output if o["action"] == "retire"),
        )
        return output

    except Exception as e:
        log.debug("Stale task detector error: %s", e)
        return []


# --- Weekly Reflection ---


def is_reflection_due(store: Store, config: GivaConfig) -> bool:
    """Return True if weekly reflection is due.

    Due when: correct day of week, correct hour, and no review for this week
    with summary containing 'weekly' (simple heuristic to avoid separate table).
    """
    now = datetime.now()
    goals_cfg = getattr(config, "goals", None)
    target_day = getattr(goals_cfg, "weekly_reflection_day", 6)  # Sunday
    target_hour = getattr(goals_cfg, "weekly_reflection_hour", 18)

    if now.weekday() != target_day:
        return False
    if now.hour < target_hour:
        return False

    # Check if we already ran this week (look for a review this week with
    # "weekly" in the prompt_text)
    week_start = now - timedelta(days=now.weekday())
    week_start_str = week_start.strftime("%Y-%m-%d")
    reviews = store.get_recent_reviews(limit=7)
    for r in reviews:
        if r.review_date >= week_start_str and "weekly" in (r.prompt_text or "").lower():
            return False

    return True


def generate_weekly_reflection(
    store: Store, config: GivaConfig,
) -> Generator[str, None, Optional[int]]:
    """Generate a weekly reflection. Yields streamed tokens.

    Reviews the week's completed tasks, goal progress, daily reviews,
    and active goals. Suggests retiring stale goals, new emerging goals,
    and strategy adjustments.

    Creates a DailyReview row (with "weekly" marker) before streaming.
    Returns the review ID via generator return value.
    """
    from giva.intelligence.profile import get_profile_summary
    from giva.llm.engine import manager
    from giva.llm.prompts import WEEKLY_REFLECTION_SYSTEM, WEEKLY_REFLECTION_USER
    from giva.llm.structured import WeeklyReflectionResult

    now = datetime.now()
    now_str = now.strftime("%A, %B %d, %Y")
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7))

    profile_summary = get_profile_summary(store) or ""
    profile_section = f"User profile:\n{profile_summary}" if profile_summary else ""

    # Tasks completed this week
    all_done = store.get_tasks(status="done", limit=100)
    week_completed = [
        t for t in all_done
        if t.created_at and t.created_at >= week_ago
    ]
    if week_completed:
        completed_lines = []
        for t in week_completed:
            goal_note = ""
            if t.goal_id:
                g = store.get_goal(t.goal_id)
                goal_note = f" (for: {g.title})" if g else ""
            completed_lines.append(f"- [{t.priority}] {t.title}{goal_note}")
        completed_tasks = "\n".join(completed_lines)
    else:
        completed_tasks = "No tasks completed this week."

    # Goal progress entries from this week
    goals = store.get_goals(status="active")
    progress_lines = []
    for g in goals:
        entries = store.get_goal_progress(g.id, limit=20)
        week_entries = [
            e for e in entries
            if e.created_at and e.created_at >= week_ago
        ]
        if week_entries:
            progress_lines.append(f"**{g.title}** ({g.tier}):")
            for e in week_entries:
                date_str = e.created_at.strftime("%b %d") if e.created_at else "?"
                progress_lines.append(f"  - {date_str} [{e.source}]: {e.note}")

    goal_progress = "\n".join(progress_lines) if progress_lines else "No progress logged."

    # Daily review summaries from this week
    reviews = store.get_recent_reviews(limit=7)
    week_reviews = [
        r for r in reviews
        if r.review_date >= week_ago.strftime("%Y-%m-%d")
        and "weekly" not in (r.prompt_text or "").lower()
    ]
    if week_reviews:
        review_lines = []
        for r in week_reviews:
            summary = r.summary or r.prompt_text[:200]
            review_lines.append(f"- {r.review_date}: {summary}")
        review_summaries = "\n".join(review_lines)
    else:
        review_summaries = "No daily reviews this week."

    # Active goals summary
    goals_summary = get_goals_summary(store, include_progress=True)
    if not goals_summary:
        goals_summary = "No active goals."

    system = WEEKLY_REFLECTION_SYSTEM.format(
        now=now_str,
        profile_section=profile_section,
        completed_tasks=completed_tasks,
        goal_progress=goal_progress,
        review_summaries=review_summaries,
        goals_summary=goals_summary,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": WEEKLY_REFLECTION_USER},
    ]

    # Create a review row with "weekly" marker in prompt_text
    review = DailyReview(
        review_date=today,
        prompt_text=(
            f"[weekly] Goals: {len(goals)} active. "
            f"Tasks completed: {len(week_completed)}. "
            f"Daily reviews: {len(week_reviews)}."
        ),
    )
    try:
        review_id = store.add_daily_review(review)
    except Exception:
        # Review for today already exists (daily + weekly on same day)
        existing = store.get_daily_review(today)
        review_id = existing.id if existing else None

    full_response = []
    for token in manager.stream_generate(
        config.llm.model, messages, max_tokens=1024, temp=0.4, top_p=0.95
    ):
        full_response.append(token)
        yield token

    # Parse reflection and save
    text = "".join(full_response)
    result = _parse_json_response(text, WeeklyReflectionResult)
    if result and review_id:
        store.update_daily_review(review_id, "", result.summary)
        log.info("Weekly reflection saved (id=%d)", review_id)

        # Log strategy updates as progress entries
        for update in result.strategy_updates:
            goal_id = update.get("goal_id")
            suggestion = update.get("suggestion", "")
            if goal_id and suggestion and store.get_goal(goal_id):
                store.add_goal_progress(
                    goal_id,
                    f"Weekly reflection: {suggestion}",
                    "reflection",
                )

    return review_id


# --- Helpers ---


def _build_plans_status(store: Store) -> str:
    """Build a compact status string for active tactical plans."""
    objectives = store.get_goals(tier="mid_term", status="active")
    if not objectives:
        return "No active mid-term objectives."

    lines = []
    for obj in objectives:
        tasks = store.get_tasks_for_goal(obj.id)
        if not tasks:
            lines.append(f"- {obj.title}: no tactical plan yet")
            continue

        done = sum(1 for t in tasks if t.status == "done")
        total = len(tasks)
        overdue = sum(
            1 for t in tasks
            if t.status in ("pending", "in_progress")
            and t.due_date and t.due_date < datetime.now()
        )
        status = f"{done}/{total} tasks done"
        if overdue:
            status += f", {overdue} overdue"
        lines.append(f"- {obj.title}: {status}")

    return "\n".join(lines) if lines else "No active plans."
