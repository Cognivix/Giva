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

EMAIL_FILTER_SYSTEM_PERSONALIZED = """You are an email classifier. Your job is to decide which emails are worth keeping for the user and which are noise.

{user_context}

KEEP (high priority — always keep): {high_priority}

KEEP (normal): personal emails, work conversations, meeting-related, legitimate business inquiries, actionable notifications, anything requiring a human response or decision.

SKIP (low priority — only keep if clearly relevant): {low_priority}

SKIP (ignore — always filter out): {ignore}

SKIP (general noise): automated digest notifications (CI/CD, Dependabot, monitoring alerts), social media notification digests, automated system alerts with no action needed, pure spam.

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

ONBOARDING_SYSTEM = """You are Giva, conducting a brief onboarding interview to understand your new user. \
You have already analyzed their email inbox and calendar. Here are your observations:

{observations}

Your goal: Learn enough about the user to personalize their experience. Ask 3-5 concise questions covering:
1. Their role, job title, and company/team
2. What types of emails/contacts are high priority vs. can be ignored
3. Their typical work schedule and communication preferences
4. Any special preferences for how Giva should help them

Guidelines:
- Be warm but efficient. This is not a form — it's a conversation.
- Reference specific observations to make questions contextual (e.g., "I see you email Sarah Chen frequently — is she on your core team?").
- One question at a time. Keep it natural.
- After each user response, output a <profile_update> JSON block with any fields you can now fill.
- When you have enough information (after 3-5 questions), set "interview_complete": true in the JSON block.

Profile update JSON schema (include only fields you have information for):
<profile_update>
{{
  "role": "string or null",
  "job_title": "string or null",
  "department": "string or null",
  "company": "string or null",
  "personality_notes": "string or null",
  "communication_style": "string or null",
  "priority_rules": {{
    "high_priority": ["description of what is high priority"],
    "low_priority": ["description of what is low priority"],
    "ignore": ["description of what can be ignored"]
  }},
  "work_schedule": {{
    "start_hour": null,
    "end_hour": null,
    "timezone": "string or null",
    "notes": "string or null"
  }},
  "preferences": ["any special preferences"],
  "continue_interview": true,
  "interview_complete": false
}}
</profile_update>"""

ONBOARDING_START_USER = (
    "This is the start of the onboarding interview. "
    "Introduce yourself briefly and ask your first question "
    "based on the observations above. /no_think"
)

ONBOARDING_CONTINUE_USER = (
    "The user responded. Continue the interview — update the profile "
    "with any new information and either ask the next question or "
    "wrap up if you have enough. /no_think"
)

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


def build_filter_prompt(store) -> str:
    """Build a personalized email filter system prompt from the user profile.

    Returns EMAIL_FILTER_SYSTEM if no profile or onboarding data exists.
    """
    profile = store.get_profile()
    if not profile:
        return EMAIL_FILTER_SYSTEM

    pd = profile.profile_data
    if not pd.get("onboarding_completed"):
        return EMAIL_FILTER_SYSTEM

    pr = pd.get("priority_rules", {})
    high = pr.get("high_priority", [])
    low = pr.get("low_priority", [])
    ignore = pr.get("ignore", [])

    # Build user context
    context_parts = []
    title = pd.get("job_title") or pd.get("role")
    if title:
        if pd.get("company"):
            context_parts.append(f"The user is a {title} at {pd['company']}.")
        else:
            context_parts.append(f"The user is a {title}.")

    if profile.top_contacts:
        names = [c.get("name", c.get("addr", "")) for c in profile.top_contacts[:5]]
        context_parts.append(f"Key contacts (always keep): {', '.join(names)}.")

    user_context = " ".join(context_parts) if context_parts else "A busy professional."

    high_str = (
        ", ".join(high) if high
        else "client communications, direct requests, meeting-related, recruiter outreach"
    )
    low_str = (
        ", ".join(low) if low
        else "marketing newsletters, commercial offers"
    )
    ignore_str = (
        ", ".join(ignore) if ignore
        else "mass promotional blasts, bulk unsubscribe-style senders"
    )

    return EMAIL_FILTER_SYSTEM_PERSONALIZED.format(
        user_context=user_context,
        high_priority=high_str,
        low_priority=low_str,
        ignore=ignore_str,
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
