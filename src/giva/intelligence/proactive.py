"""Proactive suggestion engine.

Streams personalized priorities based on tasks, upcoming events,
unread emails, and user profile context.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Generator

from giva.config import GivaConfig
from giva.db.store import Store
from giva.intelligence.profile import get_profile_summary
from giva.llm import engine
from giva.llm.prompts import build_system_prompt

log = logging.getLogger(__name__)

SUGGESTION_SYSTEM = """You are Giva's proactive assistant. Your job is to analyze the user's current situation — tasks, upcoming events, unread emails, and recent activity — and provide a prioritized action plan.

Current date and time: {now}

{profile_section}

Guidelines:
- Start with the most urgent/important items.
- Group by theme (e.g., "Meetings today", "Overdue tasks", "Emails needing response").
- Be specific: reference actual task titles, event names, sender names.
- Keep each suggestion actionable with a clear next step.
- Highlight conflicts or tight scheduling.
- If there's nothing urgent, acknowledge it and suggest proactive actions.
- Use markdown formatting with headers and bullet points."""

SUGGESTION_USER = """Here is my current situation:

{context}

Based on this, what should I focus on right now? Give me a prioritized action plan for today and tomorrow."""


def get_suggestions(
    store: Store,
    config: GivaConfig,
) -> Generator[str, None, None]:
    """Generate proactive suggestions. Yields streamed tokens.

    Gathers context from pending tasks, upcoming events, unread emails,
    and user profile, then streams suggestions via the assistant model.
    """
    context = _build_suggestion_context(store)

    if not context.strip():
        yield "No data available yet. Run `/sync` and `/extract` first to populate your emails, events, and tasks."
        return

    profile_summary = get_profile_summary(store)
    now_str = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")

    profile_section = ""
    if profile_summary:
        profile_section = f"User profile:\n{profile_summary}"

    system = SUGGESTION_SYSTEM.format(
        now=now_str,
        profile_section=profile_section,
    )
    user_content = SUGGESTION_USER.format(context=context)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    yield from engine.stream_generate(messages, config.llm)


def _build_suggestion_context(store: Store) -> str:
    """Assemble context from tasks, events, and emails for suggestion generation."""
    parts = []

    # Pending tasks (up to 15)
    tasks = store.get_tasks(status="pending", limit=15)
    if tasks:
        lines = ["## Pending Tasks"]
        for t in tasks:
            due = f" (due {t.due_date.strftime('%b %d')})" if t.due_date else ""
            pri = t.priority.upper() if t.priority == "high" else t.priority
            lines.append(f"- [{pri}] {t.title}{due}")
        parts.append("\n".join(lines))

    # Upcoming events (next 48 hours)
    upcoming = store.get_upcoming_events(days=2)
    if upcoming:
        lines = ["## Upcoming Events (next 48h)"]
        for ev in upcoming:
            start = ev.dtstart.strftime("%a %b %d, %I:%M %p") if ev.dtstart else "?"
            end = f" - {ev.dtend.strftime('%I:%M %p')}" if ev.dtend else ""
            loc = f" @ {ev.location}" if ev.location else ""
            lines.append(f"- {ev.summary}: {start}{end}{loc}")
            if ev.attendees:
                names = [a.get("name", "?") for a in ev.attendees[:5]]
                lines.append(f"  Attendees: {', '.join(names)}")
        parts.append("\n".join(lines))

    # Today's past events (for follow-up awareness)
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now()
    past_today = store.get_events_range(today_start, now)
    if past_today:
        lines = ["## Already Happened Today"]
        for ev in past_today:
            start = ev.dtstart.strftime("%I:%M %p") if ev.dtstart else "?"
            lines.append(f"- {ev.summary} at {start}")
        parts.append("\n".join(lines))

    # Unread emails (most recent 10)
    try:
        from giva.db.models import Email

        with store._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM emails
                   WHERE is_read = 0
                   ORDER BY date_sent DESC
                   LIMIT 10"""
            ).fetchall()
            unread = [Email.from_row(dict(r)) for r in rows]

        if unread:
            lines = ["## Unread Emails"]
            for e in unread:
                date_str = e.date_sent.strftime("%b %d") if e.date_sent else "?"
                flag = " [FLAGGED]" if e.is_flagged else ""
                lines.append(
                    f"- From {e.from_name or e.from_addr}: "
                    f"\"{e.subject}\"{flag} ({date_str})"
                )
            parts.append("\n".join(lines))
    except Exception as e:
        log.debug("Could not fetch unread emails: %s", e)

    return "\n\n".join(parts)
