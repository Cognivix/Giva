"""Reactive query handler: NL query → context retrieval → LLM response."""

from __future__ import annotations

import logging
import re
from typing import Generator, Optional

from giva.config import GivaConfig
from giva.db.store import Store
from giva.intelligence.context import (
    SLOT_CONVERSATION,
    effective_budget,
    estimate_tokens,
    retrieve_context,
)
from giva.intelligence.profile import get_profile_summary
from giva.llm import engine
from giva.llm.prompts import QUERY_WITH_CONTEXT, build_system_prompt

log = logging.getLogger(__name__)


def handle_query(
    query: str,
    store: Store,
    config: GivaConfig,
    goal_id: Optional[int] = None,
    task_id: Optional[int] = None,
    context_prefix: Optional[str] = None,
) -> Generator[str, None, None]:
    """Handle a natural language query. Yields streamed tokens.

    Args:
        query: The user's original query text (saved to DB as-is).
        store: Data layer.
        config: App configuration.
        goal_id: When set, messages are scoped to this goal in the DB
            and conversation history is loaded from that goal's chat.
        task_id: When set, messages are scoped to this task in the DB
            and conversation history is loaded from that task's chat.
        context_prefix: Extra context prepended to the LLM prompt
            (e.g. task/goal metadata) but NOT saved to the DB.
    """
    # Include session summary (Tier 2) in context if available
    from giva.intelligence.context import get_session_summary

    # Retrieve relevant context from the local store (budget-aware)
    context = retrieve_context(query, store, config.llm)

    session_summary = get_session_summary(store)
    if session_summary:
        context = f"Session context: {session_summary}\n\n{context}"

    # Build messages (with agent awareness if agents are registered)
    from giva.agents.registry import registry

    system = build_system_prompt(
        profile_summary=get_profile_summary(store),
        has_agents=registry.has_agents(),
    )

    # The LLM sees the enriched query (with context_prefix), but the DB
    # stores only the original query to keep conversation history clean.
    effective_query = f"{context_prefix}\n\n{query}" if context_prefix else query
    user_content = QUERY_WITH_CONTEXT.format(context=context, query=effective_query)

    messages = [
        {"role": "system", "content": system},
    ]

    # Include recent conversation history within the conversation budget
    # (scoped to goal/task if in scoped chat, global otherwise)
    budget = effective_budget(config.llm)
    conv_budget = int(budget * SLOT_CONVERSATION)
    recent = store.get_recent_messages(limit=10, goal_id=goal_id, task_id=task_id)
    conv_tokens = 0
    trimmed_messages = []
    for msg in recent:
        msg_tok = estimate_tokens(msg["content"])
        if conv_tokens + msg_tok > conv_budget:
            break
        trimmed_messages.append(msg)
        conv_tokens += msg_tok

    for msg in trimmed_messages:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_content})

    # Save the user's ORIGINAL query (without context_prefix)
    store.add_message("user", query, goal_id=goal_id, task_id=task_id)

    # Stream the response
    full_response = []
    for token in engine.stream_generate(messages, config.llm):
        full_response.append(token)
        yield token

    # Save the assistant's response (strip thinking blocks + special tokens)
    raw = "".join(full_response)
    from giva.llm.engine import strip_special_tokens
    clean = strip_special_tokens(raw)
    store.add_message("assistant", clean, goal_id=goal_id, task_id=task_id)
