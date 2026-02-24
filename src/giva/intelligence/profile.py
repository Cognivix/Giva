"""User profile builder - analyzes email patterns to build a local profile.

SQL-first approach: all core profile fields are computed via SQL aggregation.
Only topic extraction optionally uses the filter model (8B, cheap).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from giva.config import GivaConfig
from giva.db.models import UserProfile
from giva.db.store import Store

log = logging.getLogger(__name__)


def update_profile(store: Store, config: Optional[GivaConfig] = None) -> UserProfile:
    """Rebuild user profile from email patterns and save to DB.

    All core analytics use SQL — instant, no LLM cost.
    Topic extraction uses the filter model if config is provided.
    """
    email_addr, display_name = _detect_user_identity(store)
    if not email_addr:
        log.warning("Could not detect user identity — not enough emails")
        return UserProfile()

    top_contacts = _compute_top_contacts(store, exclude_addr=email_addr)
    active_hours = _compute_active_hours(store)
    avg_response = _compute_avg_response_time(store)
    volume = _compute_email_volume(store)

    # Optional LLM-based topic extraction
    topics: list[str] = []
    if config:
        try:
            topics = _extract_topics(store, config)
        except Exception as e:
            log.warning("Topic extraction failed (non-fatal): %s", e)

    # Writing style analysis from sent emails
    writing_style: dict = {}
    if config:
        try:
            writing_style = _analyze_writing_style(store, config)
        except Exception as e:
            log.warning("Writing style analysis failed (non-fatal): %s", e)

    # Preserve existing profile_data (onboarding answers, preferences, etc.)
    existing = store.get_profile()
    preserved_data = existing.profile_data if existing else {}

    # Merge writing style into preserved data
    if writing_style:
        preserved_data["writing_style"] = writing_style

    profile = UserProfile(
        display_name=display_name,
        email_address=email_addr,
        top_contacts=top_contacts,
        top_topics=topics,
        active_hours=active_hours,
        avg_response_time_min=avg_response,
        email_volume_daily=volume,
        profile_data=preserved_data,
        updated_at=datetime.now(),
    )

    store.upsert_profile(profile)
    log.info(
        "Profile updated: %s <%s>, %d contacts, %d topics",
        display_name, email_addr, len(top_contacts), len(topics),
    )
    return profile


def get_profile_summary(store: Store) -> str:
    """Get a text summary of the user profile for the LLM system prompt.

    Returns empty string if no profile exists (zero cost fallback).
    """
    profile = store.get_profile()
    if not profile or not profile.email_address:
        return ""

    lines = []
    lines.append(f"Name: {profile.display_name}")
    lines.append(f"Email: {profile.email_address}")

    if profile.top_contacts:
        contact_strs = []
        for c in profile.top_contacts[:5]:
            name = c.get("name", c.get("addr", ""))
            count = c.get("count", 0)
            contact_strs.append(f"{name} ({count} emails)")
        lines.append(f"Top contacts: {', '.join(contact_strs)}")

    if profile.top_topics:
        lines.append(f"Key topics: {', '.join(profile.top_topics[:8])}")

    if profile.active_hours:
        # Find peak hours
        sorted_hours = sorted(
            profile.active_hours.items(), key=lambda x: x[1], reverse=True
        )
        peak = [f"{h}:00" for h, _ in sorted_hours[:3]]
        lines.append(f"Most active hours: {', '.join(peak)}")

    if profile.avg_response_time_min > 0:
        if profile.avg_response_time_min < 60:
            lines.append(f"Avg response time: {profile.avg_response_time_min:.0f} minutes")
        else:
            hours = profile.avg_response_time_min / 60
            lines.append(f"Avg response time: {hours:.1f} hours")

    if profile.email_volume_daily > 0:
        lines.append(f"Email volume: ~{profile.email_volume_daily:.1f} emails/day")

    # Rich onboarding data from profile_data
    pd = profile.profile_data
    if pd.get("onboarding_completed"):
        if pd.get("role"):
            lines.append(f"Role: {pd['role']}")
        if pd.get("job_title"):
            lines.append(f"Title: {pd['job_title']}")
        if pd.get("company"):
            org = pd["company"]
            if pd.get("department"):
                org += f" / {pd['department']}"
            lines.append(f"Organization: {org}")
        if pd.get("personality_notes"):
            lines.append(f"Personality: {pd['personality_notes']}")
        if pd.get("communication_style"):
            lines.append(f"Communication style: {pd['communication_style']}")
        pr = pd.get("priority_rules", {})
        if pr.get("high_priority"):
            lines.append(f"High priority: {', '.join(pr['high_priority'])}")
        if pr.get("low_priority"):
            lines.append(f"Low priority: {', '.join(pr['low_priority'])}")
        if pr.get("ignore"):
            lines.append(f"Ignore: {', '.join(pr['ignore'])}")
        ws = pd.get("work_schedule", {})
        if ws.get("start_hour") is not None and ws.get("end_hour") is not None:
            lines.append(f"Work hours: {ws['start_hour']}:00 - {ws['end_hour']}:00")
        if ws.get("notes"):
            lines.append(f"Schedule notes: {ws['notes']}")
        if pd.get("preferences"):
            lines.append(f"Preferences: {', '.join(pd['preferences'])}")

    # Writing style (from sent email analysis)
    ws = pd.get("writing_style", {})
    if ws:
        if ws.get("tone"):
            lines.append(f"Writing tone: {ws['tone']}")
        if ws.get("communication_style"):
            lines.append(f"Style: {ws['communication_style']}")
        if ws.get("greeting_patterns"):
            lines.append(f"Greetings: {', '.join(ws['greeting_patterns'][:3])}")
        if ws.get("signoff_patterns"):
            lines.append(f"Signoffs: {', '.join(ws['signoff_patterns'][:3])}")
        if ws.get("key_phrases"):
            lines.append(f"Key phrases: {', '.join(ws['key_phrases'][:5])}")
        if ws.get("topics_initiated"):
            lines.append(f"Proactive topics: {', '.join(ws['topics_initiated'][:4])}")
        if ws.get("priority_signals"):
            lines.append(f"Priority signals: {', '.join(ws['priority_signals'][:3])}")

    # Goal counts
    try:
        goals = store.get_goals(status="active")
        if goals:
            by_tier: dict[str, int] = {}
            for g in goals:
                by_tier[g.tier] = by_tier.get(g.tier, 0) + 1
            parts = []
            if by_tier.get("long_term"):
                parts.append(f"{by_tier['long_term']} long-term")
            if by_tier.get("mid_term"):
                parts.append(f"{by_tier['mid_term']} mid-term")
            if by_tier.get("short_term"):
                parts.append(f"{by_tier['short_term']} short-term")
            if parts:
                lines.append(f"Active goals: {', '.join(parts)}")
    except Exception:
        pass

    return "\n".join(lines)


# --- SQL Analytics (no LLM needed) ---


def _detect_user_identity(store: Store) -> tuple[str, str]:
    """Detect user's email and display name from sent emails.

    Strategy:
    1. Most frequent from_addr in Sent/Sent Messages folder
    2. Fallback: most frequent address in INBOX to_addrs
    3. from_name paired with that address
    """
    with store._conn() as conn:
        # Try Sent folder first
        row = conn.execute(
            """SELECT from_addr, from_name, COUNT(*) as cnt
               FROM emails
               WHERE folder LIKE '%Sent%'
               GROUP BY from_addr
               ORDER BY cnt DESC
               LIMIT 1"""
        ).fetchone()

        if row and row["cnt"] >= 1:
            return row["from_addr"], row["from_name"] or ""

        # Fallback: most common recipient in INBOX
        row = conn.execute(
            """SELECT to_addrs, COUNT(*) as cnt
               FROM emails
               WHERE folder = 'INBOX'
               GROUP BY to_addrs
               ORDER BY cnt DESC
               LIMIT 1"""
        ).fetchone()

        if row:
            try:
                addrs = json.loads(row["to_addrs"])
                if addrs:
                    return addrs[0], ""
            except (json.JSONDecodeError, IndexError):
                pass

        return "", ""


def _compute_top_contacts(
    store: Store, exclude_addr: str = "", limit: int = 10
) -> list[dict]:
    """Get the most frequent email contacts, excluding the user's own address."""
    with store._conn() as conn:
        rows = conn.execute(
            """SELECT from_addr, from_name, COUNT(*) as cnt
               FROM emails
               WHERE from_addr != ? AND from_addr != ''
               GROUP BY from_addr
               ORDER BY cnt DESC
               LIMIT ?""",
            (exclude_addr, limit),
        ).fetchall()

        contacts = []
        for r in rows:
            contacts.append({
                "addr": r["from_addr"],
                "name": r["from_name"] or r["from_addr"],
                "count": r["cnt"],
            })
        return contacts


def _compute_active_hours(store: Store) -> dict[str, int]:
    """Compute email activity by hour of day."""
    with store._conn() as conn:
        rows = conn.execute(
            """SELECT
                 CAST(strftime('%H', date_sent) AS INTEGER) as hour,
                 COUNT(*) as cnt
               FROM emails
               WHERE date_sent IS NOT NULL AND date_sent != ''
               GROUP BY hour
               ORDER BY hour"""
        ).fetchall()

        return {str(r["hour"]): r["cnt"] for r in rows}


def _compute_avg_response_time(store: Store) -> float:
    """Compute average response time in minutes.

    Matches sent replies (via in_reply_to) to inbox originals
    and computes the time delta.
    """
    with store._conn() as conn:
        row = conn.execute(
            """SELECT AVG(
                 (julianday(sent.date_sent) - julianday(inbox.date_sent)) * 24 * 60
               ) as avg_min
               FROM emails sent
               JOIN emails inbox ON sent.in_reply_to = inbox.message_id
               WHERE sent.folder LIKE '%Sent%'
                 AND inbox.folder = 'INBOX'
                 AND julianday(sent.date_sent) > julianday(inbox.date_sent)
                 AND (julianday(sent.date_sent) - julianday(inbox.date_sent)) < 7"""
        ).fetchone()

        return max(0.0, float(row["avg_min"] or 0))


def _compute_email_volume(store: Store) -> float:
    """Compute average daily email volume."""
    with store._conn() as conn:
        row = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 julianday(MAX(date_sent)) - julianday(MIN(date_sent)) as span_days
               FROM emails
               WHERE date_sent IS NOT NULL AND date_sent != ''"""
        ).fetchone()

        total = row["total"] or 0
        span = row["span_days"] or 0
        if span > 0:
            return total / span
        return float(total)


def _extract_topics(store: Store, config: GivaConfig) -> list[str]:
    """Extract key topics from recent email subjects using the filter model.

    Uses a sample of 50 recent subjects → filter model → JSON array.
    Cheap: uses 8B model, short prompt, minimal tokens.
    """
    from giva.llm.engine import manager

    with store._conn() as conn:
        rows = conn.execute(
            "SELECT subject FROM emails ORDER BY date_sent DESC LIMIT 50"
        ).fetchall()

    if not rows:
        return []

    subjects = [r["subject"] for r in rows if r["subject"]]
    if not subjects:
        return []

    subjects_block = "\n".join(f"- {s}" for s in subjects)

    messages = [
        {
            "role": "system",
            "content": (
                "You extract key topics from email subjects. "
                "Return a JSON array of 5-10 short topic labels (2-4 words each) "
                "that capture the main themes. No explanations."
            ),
        },
        {
            "role": "user",
            "content": f"Email subjects:\n{subjects_block}\n\nJSON array of topics: /no_think",
        },
    ]

    model_id = config.llm.filter_model
    response = manager.generate(
        model_id, messages, max_tokens=256, temp=0.3, top_p=0.9
    )

    # Parse JSON array from response
    try:
        # Try direct parse first
        topics = json.loads(response.strip())
        if isinstance(topics, list):
            return [str(t) for t in topics[:10]]
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown fencing
    import re

    match = re.search(r"\[.*?\]", response, re.DOTALL)
    if match:
        try:
            topics = json.loads(match.group())
            if isinstance(topics, list):
                return [str(t) for t in topics[:10]]
        except json.JSONDecodeError:
            pass

    log.warning("Could not parse topic extraction response")
    return []


def _analyze_writing_style(store: Store, config: GivaConfig) -> dict:
    """Analyze the user's writing style from sent email bodies.

    Samples sent emails, formats them for the LLM, and extracts
    tone, greeting/signoff patterns, key phrases, and communication style.

    Returns a dict suitable for storage in ``profile_data["writing_style"]``.
    Returns ``{}`` if not enough data.
    """
    import re

    from giva.llm.engine import manager
    from giva.llm.prompts import WRITING_STYLE_SYSTEM, WRITING_STYLE_USER
    from giva.sync.mail import fetch_sent_bodies_sample

    sample_size = config.mail.writing_style_sample_size
    samples = fetch_sent_bodies_sample(store, sample_size=sample_size)

    if len(samples) < 3:
        log.debug("Not enough sent emails for writing style analysis (%d)", len(samples))
        return {}

    # Format samples for the prompt
    formatted = []
    for i, s in enumerate(samples):
        formatted.append(
            f"--- Email {i + 1} ---\n"
            f"Subject: {s['subject']}\n"
            f"Date: {s['date_sent']}\n"
            f"Body:\n{s['body']}\n"
        )
    samples_block = "\n".join(formatted)

    messages = [
        {"role": "system", "content": WRITING_STYLE_SYSTEM},
        {
            "role": "user",
            "content": WRITING_STYLE_USER.format(
                count=len(samples), samples=samples_block,
            ),
        },
    ]

    response = manager.generate(
        config.llm.filter_model,
        messages,
        max_tokens=512,
        temp=0.3,
        top_p=0.9,
    )

    # Parse JSON from response
    # Strip think tags first
    response = re.sub(r"<think>.*?</think>\s*", "", response, flags=re.DOTALL)

    try:
        result = json.loads(response.strip())
        if isinstance(result, dict):
            log.info("Writing style analysis complete: tone=%s", result.get("tone", "?"))
            return result
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object from markdown fencing
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                log.info("Writing style analysis complete: tone=%s", result.get("tone", "?"))
                return result
        except json.JSONDecodeError:
            pass

    log.warning("Could not parse writing style analysis response")
    return {}
