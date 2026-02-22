"""Reactive query handler: NL query → context retrieval → LLM response."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Generator

from giva.config import GivaConfig
from giva.db.store import Store
from giva.llm import engine
from giva.intelligence.profile import get_profile_summary
from giva.llm.prompts import (
    QUERY_WITH_CONTEXT,
    build_system_prompt,
    format_email_context,
    format_event_context,
)

log = logging.getLogger(__name__)


def handle_query(
    query: str,
    store: Store,
    config: GivaConfig,
) -> Generator[str, None, None]:
    """Handle a natural language query. Yields streamed tokens."""
    # Retrieve relevant context from the local store
    context = _retrieve_context(query, store)

    # Build messages
    system = build_system_prompt(profile_summary=get_profile_summary(store))
    user_content = QUERY_WITH_CONTEXT.format(context=context, query=query)

    messages = [
        {"role": "system", "content": system},
    ]

    # Include recent conversation history for multi-turn context
    recent = store.get_recent_messages(limit=6)
    for msg in recent:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_content})

    # Save the user's query
    store.add_message("user", query)

    # Stream the response
    full_response = []
    for token in engine.stream_generate(messages, config.llm):
        full_response.append(token)
        yield token

    # Save the assistant's response (strip <think>...</think> from conversation history)
    raw = "".join(full_response)
    clean = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL)
    store.add_message("assistant", clean)


def _retrieve_context(query: str, store: Store) -> str:
    """Retrieve relevant emails and events based on the query.

    Emails are synced headers-only. If any matched emails lack body content,
    we lazily fetch bodies from Apple Mail on demand and cache them.
    """
    parts = []

    # Try FTS5 search on emails (searches subject, from_name, from_addr + any cached body)
    search_emails = []
    try:
        search_emails = store.search_emails(query, limit=10)
        if search_emails:
            _ensure_bodies(search_emails, store)
            parts.append("Relevant emails (search results):")
            parts.append(format_email_context(search_emails))
    except Exception:
        pass  # FTS5 query syntax might not match user's natural language

    # Always include recent emails for context
    recent_emails = store.get_recent_emails(limit=5)
    if recent_emails:
        # Skip emails already in search results
        seen_ids = {e.message_id for e in search_emails}
        recent_emails = [e for e in recent_emails if e.message_id not in seen_ids]
        if recent_emails:
            _ensure_bodies(recent_emails, store)
            parts.append("Recent emails:")
            parts.append(format_email_context(recent_emails))

    # Include upcoming events
    upcoming = store.get_upcoming_events(days=7)
    if upcoming:
        parts.append("Upcoming events (next 7 days):")
        parts.append(format_event_context(upcoming))

    # Include recent past events (for follow-up questions)
    past_start = datetime.now() - timedelta(days=7)
    past_events = store.get_events_range(past_start, datetime.now())
    if past_events:
        parts.append("Recent past events (last 7 days):")
        parts.append(format_event_context(past_events))

    if not parts:
        return "No emails or events found in the local store. Try running /sync first."

    return "\n\n".join(parts)


def _ensure_bodies(emails: list, store: Store) -> None:
    """Lazily fetch and cache body content for emails that lack it."""
    from giva.sync.mail import fetch_email_body

    for email in emails:
        if email.body_plain:
            continue  # Already have the body
        body = fetch_email_body(email.message_id)
        if body:
            email.body_plain = body
            # Cache in DB so we don't fetch again
            store.update_email_body(email.message_id, body)
