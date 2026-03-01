"""Onboarding interview — LLM-driven multi-turn conversation to understand the user.

After the first sync populates emails/calendar, this module conducts a 3-5 question
interview to learn the user's role, priorities, and preferences. Answers are stored
in the existing profile_data JSON column (no schema migration needed).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Generator, Optional

from giva.config import GivaConfig
from giva.db.store import Store
from giva.llm.prompts import (
    ONBOARDING_CONTINUE_USER,
    ONBOARDING_START_USER,
    ONBOARDING_SYSTEM,
)

log = logging.getLogger(__name__)

# Tag delimiters for structured JSON in LLM output
_TAG_OPEN = "<profile_update>"
_TAG_CLOSE = "</profile_update>"


def is_onboarding_needed(store: Store) -> bool:
    """Check if onboarding should be triggered.

    Returns True if:
    - Profile doesn't exist or onboarding_completed is False, AND
    - There is synced data to analyze (at least some emails or events)
    """
    stats = store.get_stats()
    if stats["emails"] == 0 and stats["events"] == 0:
        return False  # Nothing to analyze yet

    profile = store.get_profile()
    if not profile:
        return True

    return not profile.profile_data.get("onboarding_completed", False)


def start_onboarding(
    store: Store, config: GivaConfig
) -> Generator[str, None, None]:
    """Begin the onboarding interview. Yields streamed tokens for the first question.

    1. Gathers observations from synced data
    2. Builds the onboarding system prompt
    3. Streams the LLM's first question
    4. Saves the assistant's message to onboarding_history
    """
    from giva.llm.engine import manager

    observations = _gather_observations(store)
    system_prompt = ONBOARDING_SYSTEM.format(observations=observations)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": ONBOARDING_START_USER},
    ]

    # Stream the response, filtering out <profile_update> tags
    full_response = []
    visible_text = []

    for token in manager.stream_generate(
        config.llm.model,
        messages,
        max_tokens=config.llm.max_tokens,
        temp=config.llm.temperature,
        top_p=config.llm.top_p,
    ):
        full_response.append(token)
        # Yield tokens that are visible to the user (not inside profile_update tags)
        visible, done = _filter_visible_token(full_response, visible_text)
        if visible:
            visible_text.append(visible)
            yield visible
        if done:
            break

    # Parse and save state
    full_text = "".join(full_response)
    visible_full = "".join(visible_text)

    # Clean GPT-style special tokens from saved text
    from giva.llm.engine import strip_special_tokens
    visible_full = strip_special_tokens(visible_full)

    _parse_and_save(full_text, visible_full, "assistant", store, step=1)

    # Initialize onboarding history
    store.update_profile_data({
        "onboarding_step": 1,
        "onboarding_history": [
            {"role": "assistant", "content": visible_full},
        ],
    })


def continue_onboarding(
    user_response: str, store: Store, config: GivaConfig
) -> Generator[str, None, None]:
    """Process a user answer and yield the next question or completion message.

    1. Loads current onboarding history
    2. Appends user response
    3. Streams LLM continuation
    4. Parses profile updates and merges into profile_data
    """
    from giva.llm.engine import manager

    profile = store.get_profile()
    pd = profile.profile_data if profile else {}
    history = pd.get("onboarding_history", [])
    step = pd.get("onboarding_step", 0) + 1

    log.info("continue_onboarding: step=%d, history=%d entries", step, len(history))

    # Append user response to history
    history.append({"role": "user", "content": user_response})

    # Build messages
    observations = _gather_observations(store)
    system_prompt = ONBOARDING_SYSTEM.format(observations=observations)

    messages = [{"role": "system", "content": system_prompt}]
    # Add the full conversation history
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": ONBOARDING_CONTINUE_USER})

    # Stream response
    full_response = []
    visible_text = []

    for token in manager.stream_generate(
        config.llm.model,
        messages,
        max_tokens=config.llm.max_tokens,
        temp=config.llm.temperature,
        top_p=config.llm.top_p,
    ):
        full_response.append(token)
        visible, done = _filter_visible_token(full_response, visible_text)
        if visible:
            visible_text.append(visible)
            yield visible
        if done:
            break

    full_text = "".join(full_response)
    visible_full = "".join(visible_text)

    # Clean GPT-style special tokens from saved text
    from giva.llm.engine import strip_special_tokens
    visible_full = strip_special_tokens(visible_full)

    # Parse profile update from tags
    update = _parse_and_save(full_text, visible_full, "assistant", store, step=step)

    # Update history with assistant response
    history.append({"role": "assistant", "content": visible_full})

    # Check if interview is complete
    is_complete = False
    if update and update.get("interview_complete"):
        is_complete = True

    store.update_profile_data({
        "onboarding_step": step,
        "onboarding_history": history,
        "onboarding_completed": is_complete,
        **({"onboarding_completed_at": datetime.now().isoformat()} if is_complete else {}),
    })

    # Seed initial goals from onboarding answers
    if is_complete:
        try:
            from giva.intelligence.goals import create_initial_goals

            profile = store.get_profile()
            if profile and profile.profile_data:
                count = create_initial_goals(store, profile.profile_data)
                if count > 0:
                    log.info("Seeded %d initial goals from onboarding", count)
        except Exception as e:
            log.warning("Failed to seed initial goals: %s", e)


# --- Internal helpers ---


def _gather_observations(store: Store) -> str:
    """Build a text block of observations from synced email/calendar data."""
    from giva.intelligence.profile import (
        _compute_active_hours,
        _compute_email_volume,
        _compute_top_contacts,
        _detect_user_identity,
    )

    lines = []

    # User identity
    email_addr, display_name = _detect_user_identity(store)
    if email_addr:
        name_str = f"{display_name} " if display_name else ""
        lines.append(f"User: {name_str}<{email_addr}>")

    # Top contacts
    contacts = _compute_top_contacts(store, exclude_addr=email_addr or "")
    if contacts:
        lines.append("\nTop email contacts:")
        for c in contacts[:8]:
            lines.append(f"  - {c['name']} ({c['count']} emails)")

    # Topics (from existing profile if available)
    profile = store.get_profile()
    if profile and profile.top_topics:
        lines.append(f"\nKey topics: {', '.join(profile.top_topics[:10])}")

    # Active hours
    active_hours = _compute_active_hours(store)
    if active_hours:
        sorted_hours = sorted(active_hours.items(), key=lambda x: x[1], reverse=True)
        peak = [f"{h}:00" for h, _ in sorted_hours[:3]]
        lines.append(f"\nMost active email hours: {', '.join(peak)}")

    # Email volume
    volume = _compute_email_volume(store)
    if volume > 0:
        lines.append(f"Email volume: ~{volume:.1f} emails/day")

    # Upcoming events
    events = store.get_upcoming_events(days=7)
    if events:
        lines.append(f"\nUpcoming events ({len(events)} in next 7 days):")
        for ev in events[:5]:
            start = ev.dtstart.strftime("%a %b %d, %I:%M %p") if ev.dtstart else "?"
            lines.append(f"  - {ev.summary} ({start})")
            if ev.attendees:
                names = [a.get("name", "?") for a in ev.attendees[:3]]
                lines.append(f"    Attendees: {', '.join(names)}")

    # Stats
    stats = store.get_stats()
    lines.append(f"\nTotal: {stats['emails']} emails, {stats['events']} events synced")

    # MCP source observations (Notes, iMessages, Discord)
    try:
        from giva.intelligence.mcp_observations import gather_all_mcp_observations

        mcp_obs = gather_all_mcp_observations()
        if mcp_obs:
            lines.append(f"\n{mcp_obs}")
    except Exception as exc:
        log.debug("MCP observations failed: %s", exc)

    # Apple Recents (recently used files via Spotlight)
    try:
        from giva.utils.recents import format_recent_files, get_recent_files

        recent_files = get_recent_files(hours=48, limit=10)
        if recent_files:
            lines.append(f"\n{format_recent_files(recent_files)}")
    except Exception as exc:
        log.debug("Apple Recents failed: %s", exc)

    # Writing style analysis (from sent email profiling)
    try:
        profile = store.get_profile()
        if profile and profile.profile_data:
            ws = profile.profile_data.get("writing_style", {})
            if ws:
                lines.append("\nWriting style (from sent emails):")
                if ws.get("tone"):
                    lines.append(f"  Tone: {ws['tone']}")
                if ws.get("communication_style"):
                    lines.append(f"  Style: {ws['communication_style']}")
                if ws.get("greeting_patterns"):
                    lines.append(
                        f"  Greetings: {', '.join(ws['greeting_patterns'][:3])}"
                    )
                if ws.get("signoff_patterns"):
                    lines.append(
                        f"  Signoffs: {', '.join(ws['signoff_patterns'][:3])}"
                    )
                if ws.get("topics_initiated"):
                    lines.append(
                        f"  Proactive topics: {', '.join(ws['topics_initiated'][:4])}"
                    )
                if ws.get("priority_signals"):
                    lines.append(
                        f"  Priority signals: {', '.join(ws['priority_signals'][:3])}"
                    )
    except Exception as exc:
        log.debug("Writing style observations failed: %s", exc)

    return "\n".join(lines)


def _filter_visible_token(
    full_response: list[str], visible_so_far: list[str]
) -> tuple[Optional[str], bool]:
    """Filter streaming tokens, hiding <profile_update> blocks from the user.

    Returns (visible_token_or_None, should_stop).

    The LLM may emit ``<profile_update>JSON</profile_update>`` anywhere in the
    output — before, after, or in the middle of visible text.  This function
    strips the tag block and yields all other text.

    Handles tag boundaries split across tokens by holding back any trailing
    text that could be a partial prefix of ``<profile_update>``.
    """
    text = "".join(full_response)
    visible_len = sum(len(t) for t in visible_so_far)

    # Build the "visible text" by stripping the tag block from the full text.
    # This gives us a clean visible-only string we can compare to what was
    # already yielded.
    tag_start = text.find(_TAG_OPEN)

    if tag_start == -1:
        # No tag found yet — the visible text is the entire accumulation.
        visible_text_so_far = text
    else:
        # Tag found — visible text is everything before it ...
        before_tag = text[:tag_start]
        tag_close_start = text.find(_TAG_CLOSE)
        if tag_close_start == -1:
            # Tag not yet closed — visible text is only what's before the tag
            visible_text_so_far = before_tag
        else:
            # Tag closed — visible = before tag + after closing tag
            after_tag = text[tag_close_start + len(_TAG_CLOSE):]
            visible_text_so_far = before_tag + after_tag

    # How much new visible text is available?
    new_text = visible_text_so_far[visible_len:]
    if not new_text:
        return (None, False)

    # Hold back any suffix that could be a partial start of a tag
    holdback = _partial_tag_suffix_len(new_text, _TAG_OPEN)
    safe = new_text[: len(new_text) - holdback] if holdback else new_text
    return (safe if safe else None, False)


def _partial_tag_suffix_len(text: str, tag: str) -> int:
    """Return the length of the longest suffix of *text* that matches a prefix of *tag*.

    For example, if text ends with ``"<prof"`` and tag is ``"<profile_update>"``,
    returns 5.
    """
    max_check = min(len(tag) - 1, len(text))
    for length in range(max_check, 0, -1):
        if text[-length:] == tag[:length]:
            return length
    return 0


def _parse_and_save(
    full_text: str,
    visible_text: str,
    role: str,
    store: Store,
    step: int,
) -> Optional[dict]:
    """Extract <profile_update> JSON from LLM output and merge into profile_data."""
    match = re.search(
        rf"{re.escape(_TAG_OPEN)}(.*?){re.escape(_TAG_CLOSE)}",
        full_text,
        re.DOTALL,
    )

    if not match:
        log.debug("No <profile_update> block found in onboarding response")
        return None

    json_text = match.group(1).strip()

    # Multi-level JSON parsing fallback
    update = _parse_json(json_text)
    if update is None:
        log.warning("Could not parse onboarding profile update JSON")
        return None

    # Remove transient fields before saving to profile_data
    update.pop("continue_interview", None)

    # Merge into profile_data (preserving existing keys)
    # Handle nested dicts (priority_rules, work_schedule) with merge
    profile = store.get_profile()
    pd = profile.profile_data if profile else {}

    for key, value in update.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(pd.get(key), dict):
            pd[key].update(value)
        else:
            pd[key] = value

    store.update_profile_data(pd)
    log.info("Onboarding step %d: merged %d fields into profile_data", step, len(update))
    return update


def _parse_json(text: str) -> Optional[dict]:
    """Parse JSON with multi-level fallback."""
    # Try direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fencing
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Try finding any JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None
