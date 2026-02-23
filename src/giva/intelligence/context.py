"""Budget-aware context assembly for LLM queries.

Implements adaptive context budgets based on model size and structured
slot allocation: system prompt, query, conversation, retrieved context,
and generation headroom.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from giva.config import LLMConfig
from giva.db.store import Store

log = logging.getLogger(__name__)

# --- Adaptive budget profiles ---
# Maps model param count (billions) to context budget (tokens).
# Smaller models get tighter budgets to preserve attention quality.
_BUDGET_PROFILES = [
    (1.0, 2000),    # ≤ 1B params
    (8.0, 4000),    # ≤ 8B params
    (32.0, 8000),   # ≤ 32B params
    (float("inf"), 12000),  # > 32B params
]


def _parse_param_count(model_id: str) -> Optional[float]:
    """Extract parameter count (in billions) from a model ID string.

    Examples:
        "mlx-community/Qwen3-30B-A3B-4bit" → 30.0
        "mlx-community/Qwen3-8B-4bit" → 8.0
        "mlx-community/Qwen3-0.6B-4bit" → 0.6
    """
    name = model_id.split("/")[-1] if "/" in model_id else model_id
    # Match patterns like "-8B-", "-30B-", "-0.6B-" (same logic as models.py)
    for m in re.finditer(r"(?<=-)(\d+(?:\.\d+)?)[Bb](?=-|$)", name):
        return float(m.group(1))
    # Fallback: looser match
    for m in re.finditer(r"(?<![a-zA-Z.])(\d+(?:\.\d+)?)[Bb](?![a-zA-Z])", name):
        return float(m.group(1))
    return None


def effective_budget(config: LLMConfig) -> int:
    """Return the context budget (tokens) for the current model.

    If the user explicitly set ``context_budget_tokens`` in their config
    (i.e. it differs from the default 8000), use that.  Otherwise,
    auto-detect from the assistant model's parameter count.
    """
    # Auto-detect from model name
    params = _parse_param_count(config.model)
    if params is not None:
        for threshold, budget in _BUDGET_PROFILES:
            if params <= threshold:
                return budget
    # Fallback: use the config value (default 8000)
    return config.context_budget_tokens


def estimate_tokens(text: str) -> int:
    """Conservative token estimate: ~4 chars per token for English."""
    return len(text) // 4 + 1


def truncate_to_budget(text: str, token_budget: int) -> str:
    """Truncate text to fit within a token budget.

    Returns the text trimmed to approximately ``token_budget`` tokens,
    with a "[...truncated]" marker if cut.
    """
    if not text:
        return text
    max_chars = token_budget * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...truncated]"


def format_task_context(tasks: list) -> str:
    """Format Task objects for inclusion in the LLM context window."""
    if not tasks:
        return ""
    lines = ["## Pending Tasks"]
    for t in tasks:
        due = f" (due {t.due_date.strftime('%b %d')})" if t.due_date else ""
        pri = t.priority.upper() if t.priority == "high" else t.priority
        goal = f" [goal #{t.goal_id}]" if t.goal_id else ""
        lines.append(f"- [{pri}] {t.title}{due}{goal}")
        if t.description:
            desc = t.description[:120]
            if len(t.description) > 120:
                desc += "..."
            lines.append(f"  {desc}")
    return "\n".join(lines)


# --- Budget slot allocation ---
# Percentage of total budget for each slot.
SLOT_SYSTEM = 0.05       # System prompt + profile
SLOT_QUERY = 0.05        # Current user query
SLOT_CONVERSATION = 0.25  # Recent conversation history
SLOT_RETRIEVED = 0.55     # Emails, events, tasks, goals
SLOT_HEADROOM = 0.10      # Generation headroom (reserved)


def retrieve_context(
    query: str,
    store: Store,
    config: LLMConfig,
) -> str:
    """Budget-aware context retrieval for chat queries.

    Assembles tasks, emails, events, and goals within the retrieval
    budget slot (55% of total budget).
    """
    from giva.llm.prompts import format_email_context, format_event_context

    budget = int(effective_budget(config) * SLOT_RETRIEVED)
    remaining = budget
    parts = []

    # 1. Pending tasks — always included, short and high-signal
    tasks = store.get_tasks(status="pending", limit=10)
    if tasks:
        tasks_text = format_task_context(tasks)
        tasks_tok = estimate_tokens(tasks_text)
        if tasks_tok < remaining * 0.30:
            parts.append(tasks_text)
            remaining -= tasks_tok

    # 2. FTS email search (highest relevance to query)
    search_emails = []
    try:
        search_emails = store.search_emails(query, limit=8)
        if search_emails:
            _ensure_bodies(search_emails, store)
            email_budget = int(remaining * 0.40)
            email_text = format_email_context(search_emails)
            email_text = truncate_to_budget(email_text, email_budget)
            parts.append("Relevant emails (search results):\n" + email_text)
            remaining -= estimate_tokens(email_text)
    except Exception:
        pass  # FTS query syntax might not match

    # 3. Recent emails (if not already in search results)
    from datetime import datetime, timedelta

    recent_emails = store.get_recent_emails(limit=5)
    if recent_emails:
        seen_ids = {e.message_id for e in search_emails}
        recent_emails = [e for e in recent_emails if e.message_id not in seen_ids]
        if recent_emails:
            _ensure_bodies(recent_emails, store)
            recent_budget = int(remaining * 0.30)
            recent_text = format_email_context(recent_emails)
            recent_text = truncate_to_budget(recent_text, recent_budget)
            parts.append("Recent emails:\n" + recent_text)
            remaining -= estimate_tokens(recent_text)

    # 4. Upcoming events
    upcoming = store.get_upcoming_events(days=7)
    if upcoming:
        event_budget = int(remaining * 0.40)
        event_text = format_event_context(upcoming)
        event_text = truncate_to_budget(event_text, event_budget)
        parts.append("Upcoming events (next 7 days):\n" + event_text)
        remaining -= estimate_tokens(event_text)

    # 5. Recent past events (for follow-up questions)
    past_start = datetime.now() - timedelta(days=7)
    past_events = store.get_events_range(past_start, datetime.now())
    if past_events:
        past_budget = int(remaining * 0.40)
        past_text = format_event_context(past_events)
        past_text = truncate_to_budget(past_text, past_budget)
        parts.append("Recent past events (last 7 days):\n" + past_text)
        remaining -= estimate_tokens(past_text)

    # 6. Active goals (fill remaining budget)
    try:
        from giva.intelligence.goals import get_goals_summary

        goals_summary = get_goals_summary(store, include_progress=True)
        if goals_summary:
            goals_text = truncate_to_budget(goals_summary, remaining)
            parts.append("Active goals:\n" + goals_text)
    except Exception:
        pass

    if not parts:
        return "No emails, events, or tasks found. Try running /sync first."

    return "\n\n".join(parts)


def maybe_compress_conversation(store: Store, config: LLMConfig) -> bool:
    """Compress old conversation turns into a session summary if the active window overflows.

    Returns True if compression was performed.
    """
    budget = effective_budget(config)
    conv_budget = int(budget * SLOT_CONVERSATION)
    active_budget = int(conv_budget * 0.70)  # Tier 1 gets 70% of conversation slot

    recent = store.get_recent_messages(limit=20)
    if len(recent) < 4:
        return False  # Not enough turns to compress

    # Estimate current active window size
    total_tokens = sum(estimate_tokens(m["content"]) for m in recent)
    if total_tokens <= active_budget:
        return False  # Still within budget

    # Take oldest 2 turns (1 user + 1 assistant) for compression
    to_compress = recent[:2]
    compress_text = "\n".join(
        f"{m['role'].capitalize()}: {m['content'][:300]}" for m in to_compress
    )

    # Get existing session summary
    profile = store.get_profile()
    existing_summary = ""
    if profile:
        existing_summary = profile.profile_data.get("session_summary", "")

    # Compress using filter model
    from giva.llm.engine import manager

    prompt = (
        "Compress these older conversation turns into a running session log. "
        "Keep: decisions, tasks created/completed, facts shared, topics discussed. "
        "Drop: greetings, pleasantries, verbose explanations.\n\n"
    )
    if existing_summary:
        prompt += f"Previous session summary: {existing_summary}\n\n"
    prompt += f"Turns to compress:\n{compress_text}\n\n"
    prompt += "Updated summary (max 150 words): /no_think"

    messages = [{"role": "user", "content": prompt}]

    try:
        summary = manager.generate(
            config.filter_model, messages, max_tokens=256, temp=0.2, top_p=0.9
        )
        # Clean up any think tags
        import re as _re

        summary = _re.sub(r"<think>.*?</think>\s*", "", summary, flags=_re.DOTALL)
        summary = summary.strip()

        if summary:
            store.update_profile_data({"session_summary": summary})
            # Delete the compressed messages from the conversations table
            _delete_oldest_messages(store, 2)
            log.info("Compressed %d turns into session summary (%d chars)",
                     len(to_compress), len(summary))
            return True
    except Exception as e:
        log.debug("Conversation compression failed: %s", e)

    return False


def _delete_oldest_messages(store: Store, count: int) -> None:
    """Delete the N oldest global messages from the conversations table.

    Only deletes messages with goal_id IS NULL so goal-scoped chat
    is never affected by conversation compression.
    """
    with store._conn() as conn:
        conn.execute(
            "DELETE FROM conversations WHERE id IN "
            "(SELECT id FROM conversations WHERE goal_id IS NULL "
            "ORDER BY id ASC LIMIT ?)",
            (count,),
        )


def get_session_summary(store: Store) -> str:
    """Return the current session summary (Tier 2) for context injection."""
    profile = store.get_profile()
    if profile:
        return profile.profile_data.get("session_summary", "")
    return ""


def _ensure_bodies(emails: list, store: Store) -> None:
    """Lazily fetch and cache body content for emails that lack it."""
    from giva.sync.mail import fetch_email_body

    for email in emails:
        if email.body_plain:
            continue
        body = fetch_email_body(email.message_id)
        if body:
            email.body_plain = body
            store.update_email_body(email.message_id, body)
