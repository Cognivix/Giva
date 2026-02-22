"""Mail sync: AppleScript/JXA with chunked fetching and LLM-based filtering.

Strategy:
- Fetch headers only (subject, sender, date, flags) in small chunks.
- Each chunk is classified by the filter LLM (small/fast model) as KEEP or SKIP.
- Only KEEP emails are stored in the local DB.
- Bodies are fetched lazily on demand when the LLM needs them for a query.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Optional

from giva.config import GivaConfig
from giva.db.models import Email
from giva.db.store import Store
from giva.utils.applescript import run_jxa_json

log = logging.getLogger(__name__)

# Small chunk size to avoid JXA timeouts on large mailboxes
_CHUNK_SIZE = 10


def _make_message_id(sender: str, subject: str, date: str) -> str:
    """Generate a stable message ID from email metadata when real ID unavailable."""
    raw = f"{sender}|{subject}|{date}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _parse_date(date_str: str) -> datetime:
    """Parse date string from JXA (ISO 8601)."""
    if not date_str:
        return datetime.now()
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Could not parse date: %s", date_str)
        return datetime.now()


def _extract_name(sender: str) -> str:
    """Extract display name from an email sender string like 'Name <email>'."""
    if "<" in sender:
        return sender.split("<")[0].strip().strip('"').strip("'")
    return ""


def _mailbox_accessor(mailbox_name: str) -> str:
    """Get the JXA accessor expression for a named mailbox."""
    name_lower = mailbox_name.lower()
    builtin = {
        "inbox": "mail.inbox",
        "sent": "mail.sentMailbox",
        "drafts": "mail.drafts",
        "trash": "mail.trash",
        "junk": "mail.junkMailbox",
    }
    if name_lower in builtin:
        return builtin[name_lower]
    return f'mail.mailboxes.byName("{mailbox_name}")'


# ---------------------------------------------------------------------------
# LLM-based email classification
# ---------------------------------------------------------------------------


def _classify_chunk(messages: list[dict], config: GivaConfig, store: Store) -> list[dict]:
    """Classify a chunk of email headers using the filter LLM.

    Returns only the messages classified as KEEP.
    On any LLM error, defaults to keeping all messages (fail-safe).
    Uses a personalized filter prompt when the user has completed onboarding.
    """
    from giva.llm.engine import manager
    from giva.llm.prompts import EMAIL_FILTER_USER, build_filter_prompt

    if not messages:
        return messages

    # Format emails for the prompt
    lines = []
    for i, msg in enumerate(messages):
        sender = msg.get("sender", "unknown")
        subject = msg.get("subject", "(no subject)")
        date = msg.get("date", "unknown date")
        read = "read" if msg.get("isRead") else "unread"
        flagged = ", flagged" if msg.get("isFlagged") else ""
        lines.append(f"{i}: From: {sender} | Subject: {subject} | Date: {date} | {read}{flagged}")

    emails_block = "\n".join(lines)

    filter_system = build_filter_prompt(store)

    prompt_messages = [
        {"role": "system", "content": filter_system},
        {"role": "user", "content": EMAIL_FILTER_USER.format(emails_block=emails_block)},
    ]

    try:
        response = manager.generate(
            config.llm.filter_model,
            prompt_messages,
            max_tokens=256,
            temp=0.1,  # Low temperature for deterministic classification
            top_p=0.95,
        )
        verdicts = _parse_filter_response(response, len(messages))
        kept = [msg for msg, verdict in zip(messages, verdicts) if verdict]
        skipped = len(messages) - len(kept)
        if skipped > 0:
            skipped_subjects = [
                msg.get("subject", "?")[:40]
                for msg, v in zip(messages, verdicts)
                if not v
            ]
            log.info("Filtered out %d emails: %s", skipped, skipped_subjects)
        return kept
    except Exception as e:
        log.warning("Filter LLM failed, keeping all emails in chunk: %s", e)
        return messages  # Fail-safe: keep everything


def _parse_filter_response(response: str, expected_count: int) -> list[bool]:
    """Parse the LLM's JSON classification response.

    Returns a list of booleans (True = KEEP, False = SKIP), one per email.
    On parse failure, defaults to all KEEP.
    """
    # Try to extract JSON array from response
    # The LLM might include markdown fences or extra text
    json_match = re.search(r'\[.*\]', response, re.DOTALL)
    if not json_match:
        log.warning("Could not find JSON array in filter response: %s", response[:200])
        return [True] * expected_count

    try:
        verdicts_raw = json.loads(json_match.group())
    except json.JSONDecodeError:
        log.warning("Invalid JSON in filter response: %s", json_match.group()[:200])
        return [True] * expected_count

    # Build boolean list from parsed verdicts
    result = [True] * expected_count
    for item in verdicts_raw:
        if isinstance(item, dict):
            idx = item.get("i", item.get("index", -1))
            verdict = item.get("v", item.get("verdict", "KEEP"))
            if 0 <= idx < expected_count and str(verdict).upper() == "SKIP":
                result[idx] = False

    return result


# ---------------------------------------------------------------------------
# Sync pipeline
# ---------------------------------------------------------------------------


def sync_mail_jxa(
    store: Store,
    mailboxes: list[str],
    batch_size: int = 50,
    on_progress: Optional[callable] = None,
    config: Optional[GivaConfig] = None,
) -> tuple[int, int]:
    """Sync emails from Apple Mail via JXA.

    Returns (synced_count, filtered_count).
    Uses chunked header-only fetching with LLM classification.
    """
    total_synced = 0
    total_filtered = 0
    for mailbox_name in mailboxes:
        try:
            synced, filtered = _sync_mailbox_headers(
                store, mailbox_name, batch_size, on_progress, config
            )
            total_synced += synced
            total_filtered += filtered
            store.update_sync_state(f"mail:{mailbox_name}", synced, "success")
            log.info(
                "Synced %d emails from %s (%d filtered out)",
                synced, mailbox_name, filtered,
            )
        except Exception as e:
            log.error("Failed to sync mailbox %s: %s", mailbox_name, e)
            store.update_sync_state(f"mail:{mailbox_name}", 0, f"error: {e}")
    return total_synced, total_filtered


def _sync_mailbox_headers(
    store: Store,
    mailbox_name: str,
    total_count: int,
    on_progress: Optional[callable] = None,
    config: Optional[GivaConfig] = None,
) -> tuple[int, int]:
    """Sync headers from a mailbox in small chunks with LLM filtering.

    Returns (synced_count, filtered_count).
    """
    accessor = _mailbox_accessor(mailbox_name)
    synced = 0
    filtered = 0
    offset = 0

    while offset < total_count:
        chunk_size = min(_CHUNK_SIZE, total_count - offset)
        try:
            messages = _fetch_headers_chunk(accessor, offset, chunk_size)
        except Exception as e:
            log.warning("Chunk at offset %d failed: %s. Stopping.", offset, e)
            break

        if not messages:
            break

        # LLM-based filtering (if config provided)
        if config is not None:
            kept = _classify_chunk(messages, config, store)
            filtered += len(messages) - len(kept)
        else:
            kept = messages

        for msg in kept:
            message_id = msg.get("messageId") or _make_message_id(
                msg.get("sender", ""), msg.get("subject", ""), msg.get("date", "")
            )
            email_obj = Email(
                message_id=message_id,
                folder=mailbox_name,
                from_addr=msg.get("sender", ""),
                from_name=_extract_name(msg.get("sender", "")),
                to_addrs=msg.get("toAddrs", []),
                cc_addrs=msg.get("ccAddrs", []),
                subject=msg.get("subject", "(no subject)"),
                date_sent=_parse_date(msg.get("date", "")),
                body_plain="",  # Body fetched lazily on demand
                is_read=msg.get("isRead", False),
                is_flagged=msg.get("isFlagged", False),
            )
            store.upsert_email(email_obj)
            synced += 1

        offset += len(messages)
        if on_progress:
            on_progress(synced, filtered, total_count)

    return synced, filtered


def _fetch_headers_chunk(accessor: str, offset: int, count: int) -> list[dict]:
    """Fetch a chunk of email headers (no body) via JXA."""
    script = f"""
var mail = Application("Mail");
var mailbox = {accessor};
var msgs = mailbox.messages();
var total = msgs.length;
var start = {offset};
var end = Math.min(start + {count}, total);
var results = [];
for (var i = start; i < end; i++) {{
    try {{
        var m = msgs[i];
        var toRecips = m.toRecipients();
        var toAddrs = [];
        for (var j = 0; j < toRecips.length; j++) {{
            toAddrs.push(toRecips[j].address());
        }}
        var ccRecips = m.ccRecipients();
        var ccAddrs = [];
        for (var k = 0; k < ccRecips.length; k++) {{
            ccAddrs.push(ccRecips[k].address());
        }}
        results.push({{
            messageId: m.messageId(),
            subject: m.subject() || "(no subject)",
            sender: m.sender(),
            date: m.dateReceived() ? m.dateReceived().toISOString() : "",
            toAddrs: toAddrs,
            ccAddrs: ccAddrs,
            isRead: m.readStatus(),
            isFlagged: m.flaggedStatus()
        }});
    }} catch(e) {{
        // Skip messages that fail to parse
    }}
}}
JSON.stringify(results);
"""
    return run_jxa_json(script, timeout=30)


def fetch_email_body(message_id: str) -> Optional[str]:
    """Lazily fetch the body of a single email by message ID.

    Called on-demand when the LLM needs body content for a query.
    The result should be cached in the DB by the caller.
    """
    script = f"""
var mail = Application("Mail");
var msgs = mail.inbox.messages.whose({{messageId: "{message_id}"}})();
if (msgs.length > 0) {{
    JSON.stringify({{content: msgs[0].content() || ""}});
}} else {{
    JSON.stringify({{content: ""}});
}}
"""
    try:
        result = run_jxa_json(script, timeout=15)
        return result.get("content", "")
    except Exception as e:
        log.warning("Could not fetch body for %s: %s", message_id, e)
        return None


def get_mail_account_info() -> Optional[list[dict]]:
    """Get info about configured mail accounts."""
    script = """
var mail = Application("Mail");
var accounts = mail.accounts();
var results = [];
for (var i = 0; i < accounts.length; i++) {
    results.push({
        name: accounts[i].name(),
        email: accounts[i].emailAddresses()[0] || ""
    });
}
JSON.stringify(results);
"""
    try:
        return run_jxa_json(script, timeout=10)
    except Exception as e:
        log.warning("Could not get mail accounts: %s", e)
        return None
