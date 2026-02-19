"""Prompt templates for Giva."""

from __future__ import annotations

from datetime import datetime

SYSTEM_PROMPT = """You are Giva (Generative Intelligent Virtual Assistant), a personal email and calendar assistant. You help the user understand their emails, calendar events, and tasks.

Current date and time: {now}

{profile_section}

Guidelines:
- Be concise and actionable in your responses.
- When referring to emails, mention the sender and subject.
- When referring to meetings, mention the title, time, and attendees.
- If you don't have enough information to answer, say so clearly.
- Prioritize recent and urgent items.
- Use natural, conversational language."""

EMAIL_FILTER_SYSTEM = """You are an email classifier. Your job is to decide which emails are worth keeping for a busy professional and which are noise.

KEEP: personal emails, work conversations, meeting-related, recruiter outreach, legitimate business inquiries, actionable notifications, commercial offers that could be relevant (even with low probability), client communications, anything requiring a human response or decision.

SKIP: automated digest notifications (CI/CD, Dependabot, monitoring alerts), marketing newsletters the user didn't write, mass promotional blasts, social media notification digests, automated system alerts with no action needed, bulk "unsubscribe" style senders, pure spam.

When in doubt, KEEP. It is better to keep a borderline email than to lose an important one."""

EMAIL_FILTER_USER = """Classify each email below as KEEP or SKIP.

{emails_block}

Respond with ONLY a JSON array, one entry per email in the same order:
[{{"i":0,"v":"KEEP"}},{{"i":1,"v":"SKIP"}},...] /no_think"""

TASK_EXTRACT_SYSTEM = """You are a task extraction assistant. Your job is to analyze emails and calendar events and identify concrete, actionable tasks that the user needs to complete.

Current date and time: {now}

Rules:
- Only extract genuinely actionable items that require the user to DO something.
- DO NOT create tasks for informational emails, newsletters, or FYI messages.
- DO NOT create tasks for events that are simply "attend meeting" unless there is specific prep work mentioned.
- For events, look for preparation tasks, follow-up items, or deliverables mentioned in the description.
- Set priority based on urgency and importance: "high" for deadlines within 2 days or explicit urgency, "medium" for normal work items, "low" for nice-to-have or distant deadlines.
- If a specific due date is mentioned, include it in ISO 8601 format (YYYY-MM-DD).
- Keep task titles concise (under 80 chars) and action-oriented (start with a verb).
- Include a brief source_quote from the original content that justifies the task.
- If no actionable items exist, return an empty tasks list with has_actionable_items: false."""

TASK_EXTRACT_USER = """Analyze the following {source_type_plural} and extract actionable tasks for the user.

{items_block}

Respond with ONLY a JSON object matching this schema:
{{
  "tasks": [
    {{
      "title": "string - concise action-oriented title",
      "description": "string or null - additional context",
      "priority": "high" | "medium" | "low",
      "due_date": "YYYY-MM-DD or null",
      "source_quote": "string - brief quote justifying this task"
    }}
  ],
  "has_actionable_items": true/false
}} /no_think"""

QUERY_WITH_CONTEXT = """Here is relevant context from the user's email and calendar:

{context}

User's question: {query}

Answer based on the context above. Be specific and reference actual emails/events when relevant."""


def build_system_prompt(profile_summary: str = "") -> str:
    """Build the system prompt with current time and optional profile."""
    profile_section = ""
    if profile_summary:
        profile_section = f"User profile:\n{profile_summary}"
    return SYSTEM_PROMPT.format(
        now=datetime.now().strftime("%A, %B %d, %Y at %I:%M %p"),
        profile_section=profile_section,
    )


def format_email_context(emails: list) -> str:
    """Format a list of Email objects for inclusion in a prompt."""
    if not emails:
        return "No relevant emails found."
    lines = []
    for e in emails:
        date_str = e.date_sent.strftime("%b %d, %Y %I:%M %p") if e.date_sent else "unknown date"
        read_marker = "" if e.is_read else " [UNREAD]"
        flag_marker = " [FLAGGED]" if e.is_flagged else ""
        lines.append(f"- From: {e.from_name or e.from_addr} ({e.from_addr})")
        lines.append(f"  Subject: {e.subject}{read_marker}{flag_marker}")
        lines.append(f"  Date: {date_str}")
        if e.body_plain:
            # Truncate body for context window efficiency
            body = e.body_plain[:500]
            if len(e.body_plain) > 500:
                body += "..."
            lines.append(f"  Body: {body}")
        lines.append("")
    return "\n".join(lines)


def format_event_context(events: list) -> str:
    """Format a list of Event objects for inclusion in a prompt."""
    if not events:
        return "No relevant calendar events found."
    lines = []
    for ev in events:
        start = ev.dtstart.strftime("%a %b %d, %I:%M %p") if ev.dtstart else "unknown"
        end = ev.dtend.strftime("%I:%M %p") if ev.dtend else ""
        time_range = f"{start} - {end}" if end else start
        lines.append(f"- {ev.summary}")
        lines.append(f"  Calendar: {ev.calendar_name}")
        lines.append(f"  Time: {time_range}")
        if ev.location:
            lines.append(f"  Location: {ev.location}")
        if ev.attendees:
            names = [a.get("name", "unknown") for a in ev.attendees[:5]]
            lines.append(f"  Attendees: {', '.join(names)}")
        if ev.description:
            desc = ev.description[:300]
            if len(ev.description) > 300:
                desc += "..."
            lines.append(f"  Notes: {desc}")
        lines.append("")
    return "\n".join(lines)


def format_emails_for_extraction(emails: list) -> str:
    """Format emails for task extraction (includes body content)."""
    if not emails:
        return "No emails."
    lines = []
    for i, e in enumerate(emails):
        date_str = e.date_sent.strftime("%b %d, %Y %I:%M %p") if e.date_sent else "unknown"
        lines.append(f"--- Email {i} ---")
        lines.append(f"From: {e.from_name or e.from_addr} <{e.from_addr}>")
        lines.append(f"Subject: {e.subject}")
        lines.append(f"Date: {date_str}")
        read_marker = "read" if e.is_read else "UNREAD"
        flag_marker = ", FLAGGED" if e.is_flagged else ""
        lines.append(f"Status: {read_marker}{flag_marker}")
        if e.body_plain:
            body = e.body_plain[:1000]
            if len(e.body_plain) > 1000:
                body += "\n[...truncated...]"
            lines.append(f"Body:\n{body}")
        lines.append("")
    return "\n".join(lines)


def format_events_for_extraction(events: list) -> str:
    """Format events for task extraction."""
    if not events:
        return "No events."
    lines = []
    for i, ev in enumerate(events):
        start = ev.dtstart.strftime("%a %b %d, %I:%M %p") if ev.dtstart else "unknown"
        end = ev.dtend.strftime("%I:%M %p") if ev.dtend else ""
        lines.append(f"--- Event {i} ---")
        lines.append(f"Title: {ev.summary}")
        lines.append(f"Calendar: {ev.calendar_name}")
        lines.append(f"Time: {start}" + (f" - {end}" if end else ""))
        if ev.location:
            lines.append(f"Location: {ev.location}")
        if ev.organizer:
            lines.append(f"Organizer: {ev.organizer}")
        if ev.attendees:
            names = [a.get("name", "unknown") for a in ev.attendees[:10]]
            lines.append(f"Attendees: {', '.join(names)}")
        if ev.description:
            desc = ev.description[:800]
            if len(ev.description) > 800:
                desc += "\n[...truncated...]"
            lines.append(f"Description:\n{desc}")
        lines.append("")
    return "\n".join(lines)
