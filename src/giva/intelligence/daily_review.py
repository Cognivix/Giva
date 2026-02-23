"""Daily review intelligence: scheduled reviews, background strategy, plan status checks.

Provides functions to:
- Check if a daily review is due
- Generate and stream a daily review prompt
- Save review responses and extract goal progress
- Run background strategy generation for goals without strategies
- Review tactical plan status for stalled objectives
"""

from __future__ import annotations

import logging
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
        plans_status=plans_status,
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
