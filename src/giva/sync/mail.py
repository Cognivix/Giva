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


def fetch_email_body(message_id: str, folder: str = "INBOX") -> Optional[str]:
    """Lazily fetch the body of a single email by message ID.

    Called on-demand when the LLM needs body content for a query.
    The result should be cached in the DB by the caller.

    Args:
        message_id: The email's RFC Message-ID.
        folder: Mailbox name to search first (e.g. "INBOX", "Sent").
            Falls back to cross-account search if not found.
    """
    accessor = _mailbox_accessor(folder)

    # Try specific folder first (fast)
    script = f"""
var mail = Application("Mail");
var mailbox = {accessor};
var msgs = mailbox.messages.whose({{messageId: "{message_id}"}})();
if (msgs.length > 0) {{
    JSON.stringify({{content: msgs[0].content() || ""}});
}} else {{
    JSON.stringify({{content: ""}});
}}
"""
    try:
        result = run_jxa_json(script, timeout=15)
        content = result.get("content", "")
        if content:
            return content
    except Exception as e:
        log.debug("Folder-specific body fetch failed for %s in %s: %s",
                  message_id, folder, e)

    # Fallback: cross-account search (checks all accounts/mailboxes)
    fallback_script = f"""
var mail = Application("Mail");
var accounts = mail.accounts();
var body = "";
for (var a = 0; a < accounts.length && !body; a++) {{
    var mboxes = accounts[a].mailboxes();
    for (var b = 0; b < mboxes.length && !body; b++) {{
        try {{
            var msgs = mboxes[b].messages.whose({{messageId: "{message_id}"}})();
            if (msgs.length > 0) {{
                body = msgs[0].content() || "";
            }}
        }} catch(e) {{}}
    }}
}}
JSON.stringify({{content: body}});
"""
    try:
        result = run_jxa_json(fallback_script, timeout=30)
        return result.get("content", "")
    except Exception as e:
        log.warning("Could not fetch body for %s: %s", message_id, e)
        return None


# ---------------------------------------------------------------------------
# Date-filtered fetching for initial/deep sync
# ---------------------------------------------------------------------------


def _fetch_headers_since(
    accessor: str, cutoff_iso: str, offset: int, count: int,
) -> tuple[list[dict], int]:
    """Fetch email headers newer than *cutoff_iso* using JXA date filtering.

    Args:
        accessor: JXA mailbox accessor expression (e.g. ``mail.inbox``).
        cutoff_iso: ISO 8601 cutoff date string (inclusive lower bound).
        offset: Pagination offset within the filtered results.
        count: Max messages to return in this chunk.

    Returns:
        ``(messages_list, total_matching)`` — the chunk and the total count
        of messages matching the date filter in this mailbox.
    """
    script = f"""
var mail = Application("Mail");
var mailbox = {accessor};
var cutoff = new Date("{cutoff_iso}");
var allMsgs = mailbox.messages.whose({{dateReceived: {{_greaterThan: cutoff}}}})();
var total = allMsgs.length;
var start = {offset};
var end = Math.min(start + {count}, total);
var results = [];
for (var i = start; i < end; i++) {{
    try {{
        var m = allMsgs[i];
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
    }} catch(e) {{}}
}}
JSON.stringify({{results: results, total: total}});
"""
    raw = run_jxa_json(script, timeout=60)
    return raw.get("results", []), raw.get("total", 0)


def sync_mail_initial(
    store: Store,
    mailboxes: list[str],
    months: int = 4,
    on_progress: Optional[callable] = None,
    config: Optional[GivaConfig] = None,
) -> tuple[int, int]:
    """Bootstrap sync: fetch all messages from the past *months* months.

    Unlike :func:`sync_mail_jxa` (which caps at ``batch_size`` per mailbox),
    this fetches **all** messages in the time window across all folders.
    Each chunk is LLM-filtered (if *config* provided).

    Returns ``(synced_count, filtered_count)``.
    """
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=months * 30)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    total_synced = 0
    total_filtered = 0

    for mailbox_name in mailboxes:
        accessor = _mailbox_accessor(mailbox_name)
        offset = 0

        try:
            while True:
                messages, matching_total = _fetch_headers_since(
                    accessor, cutoff_iso, offset, _CHUNK_SIZE,
                )

                if not messages:
                    break

                # LLM-based filtering
                if config is not None:
                    kept = _classify_chunk(messages, config, store)
                    total_filtered += len(messages) - len(kept)
                else:
                    kept = messages

                for msg in kept:
                    message_id = msg.get("messageId") or _make_message_id(
                        msg.get("sender", ""),
                        msg.get("subject", ""),
                        msg.get("date", ""),
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
                        body_plain="",
                        is_read=msg.get("isRead", False),
                        is_flagged=msg.get("isFlagged", False),
                    )
                    store.upsert_email(email_obj)
                    total_synced += 1

                offset += len(messages)
                if on_progress:
                    on_progress(total_synced, total_filtered, matching_total)

                # Stop if we've fetched everything
                if offset >= matching_total:
                    break

            store.update_sync_state(
                f"mail_depth:{mailbox_name}", total_synced, f"initial_{months}mo"
            )
            log.info(
                "Initial sync %s: %d messages from past %d months",
                mailbox_name, total_synced, months,
            )
        except Exception as e:
            log.error("Initial sync failed for %s: %s", mailbox_name, e)
            store.update_sync_state(
                f"mail_depth:{mailbox_name}", 0, f"error: {e}"
            )

    return total_synced, total_filtered


def fetch_sent_bodies_sample(
    store: Store,
    sample_size: int = 20,
) -> list[dict]:
    """Sample sent email bodies for writing style analysis.

    Fetches bodies (lazy-loading from Apple Mail as needed) for a sample
    of the user's Sent emails.  Filters out trivial messages.

    Returns list of ``{message_id, subject, body, date_sent}`` dicts.
    """
    with store._conn() as conn:
        rows = conn.execute(
            """SELECT id, message_id, subject, body_plain, date_sent, folder
               FROM emails
               WHERE folder LIKE '%Sent%'
                 AND length(subject) > 3
               ORDER BY date_sent DESC
               LIMIT ?""",
            (sample_size * 3,),  # Over-fetch to account for filtering
        ).fetchall()

    if not rows:
        return []

    samples = []
    for row in rows:
        body = row["body_plain"] or ""

        # Lazy-fetch body if missing
        if not body:
            fetched = fetch_email_body(row["message_id"], folder=row["folder"])
            if fetched:
                body = fetched
                store.update_email_body(row["message_id"], body)

        # Filter trivial messages
        if len(body.strip()) < 50:
            continue

        # Cap body at 2000 chars for LLM context
        samples.append({
            "message_id": row["message_id"],
            "subject": row["subject"],
            "body": body[:2000],
            "date_sent": row["date_sent"],
        })

        if len(samples) >= sample_size:
            break

    return samples


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
