"""Task extraction from emails and events via LLM.

Processes unprocessed emails/events one at a time through the assistant LLM
to identify actionable tasks. Uses structured JSON output parsed into
Pydantic models, with multi-level fail-safe parsing.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from giva.config import GivaConfig
from giva.db.models import Task
from giva.db.store import Store
from giva.llm.structured import TaskExtractionResult, ExtractedTask

log = logging.getLogger(__name__)

# Per-run limits: how many items to process per extract_tasks() call.
# Each item gets its own LLM call for exact source attribution.
_EMAIL_BATCH_SIZE = 5
_EVENT_BATCH_SIZE = 10


def extract_tasks(
    store: Store,
    config: GivaConfig,
    on_progress: Optional[callable] = None,
) -> int:
    """Extract tasks from unprocessed emails and events.

    Returns total count of new tasks created.
    """
    total = 0
    total += _extract_from_emails(store, config, on_progress)
    total += _extract_from_events(store, config, on_progress)
    if total > 0:
        log.info("Extracted %d new tasks", total)
    return total


def _extract_from_emails(
    store: Store,
    config: GivaConfig,
    on_progress: Optional[callable] = None,
) -> int:
    """Process unprocessed emails one at a time. Returns new task count."""
    unprocessed_ids = store.get_unprocessed_email_ids(limit=_EMAIL_BATCH_SIZE)
    if not unprocessed_ids:
        return 0

    count = 0
    for i, eid in enumerate(unprocessed_ids):
        email = store.get_email_by_id(eid)
        if not email:
            store.mark_extracted("email", eid, 0)
            continue

        # Lazy-fetch body if missing
        _ensure_bodies([email], store)

        # Run LLM extraction on single email
        result = _run_extraction([email], "email", config)

        task_count = 0
        if result.has_actionable_items:
            for extracted in result.tasks:
                task = Task(
                    title=extracted.title,
                    description=extracted.description or "",
                    source_type="email",
                    source_id=email.id,
                    priority=(
                        extracted.priority.value
                        if hasattr(extracted.priority, "value")
                        else extracted.priority
                    ),
                    due_date=_parse_due_date(extracted.due_date),
                )
                store.add_task(task)
                task_count += 1

        store.mark_extracted("email", email.id, task_count)
        count += task_count

        if on_progress:
            on_progress(i + 1, len(unprocessed_ids), "email", count)

    return count


def _extract_from_events(
    store: Store,
    config: GivaConfig,
    on_progress: Optional[callable] = None,
) -> int:
    """Process unprocessed events one at a time. Returns new task count."""
    unprocessed_ids = store.get_unprocessed_event_ids(limit=_EVENT_BATCH_SIZE)
    if not unprocessed_ids:
        return 0

    count = 0
    for i, eid in enumerate(unprocessed_ids):
        event = store.get_event_by_id(eid)
        if not event:
            store.mark_extracted("event", eid, 0)
            continue

        result = _run_extraction([event], "event", config)

        task_count = 0
        if result.has_actionable_items:
            for extracted in result.tasks:
                task = Task(
                    title=extracted.title,
                    description=extracted.description or "",
                    source_type="event",
                    source_id=event.id,
                    priority=(
                        extracted.priority.value
                        if hasattr(extracted.priority, "value")
                        else extracted.priority
                    ),
                    due_date=_parse_due_date(extracted.due_date),
                )
                store.add_task(task)
                task_count += 1

        store.mark_extracted("event", event.id, task_count)
        count += task_count

        if on_progress:
            on_progress(i + 1, len(unprocessed_ids), "event", count)

    return count


def _run_extraction(
    items: list,
    source_type: str,
    config: GivaConfig,
) -> TaskExtractionResult:
    """Run the LLM to extract tasks from a list of items.

    Returns a TaskExtractionResult. On any failure, returns an empty result.
    """
    from giva.llm.engine import manager
    from giva.llm.prompts import (
        TASK_EXTRACT_SYSTEM,
        TASK_EXTRACT_USER,
        format_emails_for_extraction,
        format_events_for_extraction,
    )

    if source_type == "email":
        items_block = format_emails_for_extraction(items)
        source_type_plural = "emails"
    else:
        items_block = format_events_for_extraction(items)
        source_type_plural = "calendar events"

    now_str = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    messages = [
        {"role": "system", "content": TASK_EXTRACT_SYSTEM.format(now=now_str)},
        {
            "role": "user",
            "content": TASK_EXTRACT_USER.format(
                source_type_plural=source_type_plural,
                items_block=items_block,
            ),
        },
    ]

    try:
        response = manager.generate(
            config.llm.model,  # Use assistant model, not filter model
            messages,
            max_tokens=1024,
            temp=0.3,  # Low temperature for structured output
            top_p=0.95,
        )
        return _parse_extraction_response(response)
    except Exception as e:
        log.warning("Task extraction LLM failed: %s", e)
        return TaskExtractionResult()


def _parse_extraction_response(response: str) -> TaskExtractionResult:
    """Parse the LLM's JSON response into a TaskExtractionResult.

    Multi-level fail-safe:
    1. Regex to extract JSON object (handles markdown fences, extra text)
    2. json.loads for parsing
    3. Pydantic model_validate for validation
    4. Per-task salvaging on partial validation failure
    5. Empty result on total failure
    """
    # Try to extract JSON object from response
    json_match = re.search(r"\{.*\}", response, re.DOTALL)
    if not json_match:
        log.warning("No JSON object in extraction response: %s", response[:200])
        return TaskExtractionResult()

    try:
        raw = json.loads(json_match.group())
    except json.JSONDecodeError:
        log.warning("Invalid JSON in extraction response: %s", json_match.group()[:200])
        return TaskExtractionResult()

    try:
        return TaskExtractionResult.model_validate(raw)
    except Exception as e:
        log.warning("Could not validate extraction result: %s", e)
        # Try to salvage individual tasks
        tasks = []
        for t in raw.get("tasks", []):
            try:
                tasks.append(ExtractedTask.model_validate(t))
            except Exception:
                pass
        return TaskExtractionResult(
            tasks=tasks,
            has_actionable_items=len(tasks) > 0,
        )


def _parse_due_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 date string from the LLM. Returns None on failure."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        log.debug("Could not parse due date: %s", date_str)
        return None


def _ensure_bodies(emails: list, store: Store) -> None:
    """Lazily fetch and cache body content for emails that lack it.

    Reuses the same pattern from intelligence/queries.py.
    """
    from giva.sync.mail import fetch_email_body

    for email in emails:
        if email.body_plain:
            continue
        body = fetch_email_body(email.message_id)
        if body:
            email.body_plain = body
            store.update_email_body(email.message_id, body)
