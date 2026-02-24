"""Tests for expanded mail sync: date-filtered fetch, initial sync, body fetch, sent sampling."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from giva.config import GivaConfig, MailConfig
from giva.sync.mail import (
    _fetch_headers_since,
    _mailbox_accessor,
    fetch_email_body,
    fetch_sent_bodies_sample,
    sync_mail_initial,
)


# --- _mailbox_accessor ---


def test_mailbox_accessor_inbox():
    assert _mailbox_accessor("INBOX") == "mail.inbox"


def test_mailbox_accessor_sent():
    assert _mailbox_accessor("Sent") == "mail.sentMailbox"


def test_mailbox_accessor_drafts():
    assert _mailbox_accessor("Drafts") == "mail.drafts"


def test_mailbox_accessor_trash():
    assert _mailbox_accessor("Trash") == "mail.trash"


def test_mailbox_accessor_junk():
    assert _mailbox_accessor("Junk") == "mail.junkMailbox"


def test_mailbox_accessor_custom():
    assert _mailbox_accessor("Archive") == 'mail.mailboxes.byName("Archive")'


def test_mailbox_accessor_case_insensitive():
    assert _mailbox_accessor("inbox") == "mail.inbox"
    assert _mailbox_accessor("sent") == "mail.sentMailbox"


# --- MailConfig new fields ---


def test_mail_config_defaults():
    """New MailConfig fields should have correct defaults."""
    cfg = MailConfig()
    assert cfg.initial_sync_months == 4
    assert cfg.deep_sync_max_months == 24
    assert cfg.writing_style_sample_size == 20
    assert cfg.mailboxes == ["INBOX", "Sent", "Drafts", "Archive"]


def test_mail_config_backward_compat():
    """MailConfig should work with only old fields (backward compat)."""
    cfg = MailConfig(mailboxes=["INBOX", "Sent"], batch_size=25)
    assert cfg.batch_size == 25
    assert cfg.initial_sync_months == 4  # default
    assert cfg.deep_sync_max_months == 24  # default


# --- fetch_email_body (folder-aware) ---


@patch("giva.sync.mail.run_jxa_json")
def test_fetch_email_body_specific_folder(mock_jxa):
    """Should search specified folder first."""
    mock_jxa.return_value = {"content": "Hello world body text"}

    body = fetch_email_body("msg-123@test", folder="Sent")
    assert body == "Hello world body text"
    # Check the JXA script used the Sent accessor
    script = mock_jxa.call_args[0][0]
    assert "mail.sentMailbox" in script


@patch("giva.sync.mail.run_jxa_json")
def test_fetch_email_body_default_folder(mock_jxa):
    """Should default to INBOX if no folder specified."""
    mock_jxa.return_value = {"content": "Inbox body"}

    body = fetch_email_body("msg-456@test")
    assert body == "Inbox body"
    script = mock_jxa.call_args[0][0]
    assert "mail.inbox" in script


@patch("giva.sync.mail.run_jxa_json")
def test_fetch_email_body_fallback_cross_account(mock_jxa):
    """Should fall back to cross-account search if folder search returns empty."""
    # First call (folder-specific) returns empty, second (cross-account) returns body
    mock_jxa.side_effect = [
        {"content": ""},
        {"content": "Found via cross-account"},
    ]

    body = fetch_email_body("msg-789@test", folder="Drafts")
    assert body == "Found via cross-account"
    assert mock_jxa.call_count == 2


@patch("giva.sync.mail.run_jxa_json")
def test_fetch_email_body_error_handling(mock_jxa):
    """Should return None on JXA error."""
    mock_jxa.side_effect = Exception("JXA timeout")

    body = fetch_email_body("msg-err@test")
    assert body is None


# --- _fetch_headers_since ---


@patch("giva.sync.mail.run_jxa_json")
def test_fetch_headers_since_returns_chunk_and_total(mock_jxa):
    """Should return a chunk of messages and the total matching count."""
    mock_jxa.return_value = {
        "results": [
            {"messageId": "m1", "subject": "Test 1", "sender": "a@test.com",
             "date": "2026-01-15T10:00:00Z", "toAddrs": [], "ccAddrs": [],
             "isRead": True, "isFlagged": False},
            {"messageId": "m2", "subject": "Test 2", "sender": "b@test.com",
             "date": "2026-01-16T10:00:00Z", "toAddrs": [], "ccAddrs": [],
             "isRead": False, "isFlagged": True},
        ],
        "total": 50,
    }

    messages, total = _fetch_headers_since(
        "mail.inbox", "2025-12-01T00:00:00", 0, 10,
    )
    assert len(messages) == 2
    assert total == 50
    assert messages[0]["messageId"] == "m1"


@patch("giva.sync.mail.run_jxa_json")
def test_fetch_headers_since_empty(mock_jxa):
    """Should return empty list when no messages match."""
    mock_jxa.return_value = {"results": [], "total": 0}

    messages, total = _fetch_headers_since(
        "mail.inbox", "2026-01-01T00:00:00", 0, 10,
    )
    assert messages == []
    assert total == 0


# --- sync_mail_initial ---


@patch("giva.sync.mail._fetch_headers_since")
@patch("giva.sync.mail._classify_chunk")
def test_sync_mail_initial_basic(mock_classify, mock_fetch):
    """Should sync messages from past N months across mailboxes."""
    mock_fetch.return_value = (
        [
            {"messageId": "m1", "subject": "Test", "sender": "a@test.com",
             "date": "2026-01-15T10:00:00Z", "toAddrs": [], "ccAddrs": [],
             "isRead": True, "isFlagged": False},
        ],
        1,
    )
    mock_classify.side_effect = lambda msgs, cfg, store: msgs  # keep all

    store = MagicMock()
    config = GivaConfig()

    synced, filtered = sync_mail_initial(
        store, ["INBOX"], months=4, config=config,
    )

    assert synced >= 1
    assert filtered == 0
    assert store.upsert_email.called
    assert store.update_sync_state.called


@patch("giva.sync.mail._fetch_headers_since")
def test_sync_mail_initial_no_config_no_filter(mock_fetch):
    """Should keep all messages when no config is provided (no LLM filter)."""
    mock_fetch.return_value = (
        [
            {"messageId": "m1", "subject": "Test", "sender": "a@test.com",
             "date": "2026-01-15T10:00:00Z", "toAddrs": [], "ccAddrs": [],
             "isRead": True, "isFlagged": False},
        ],
        1,
    )

    store = MagicMock()

    synced, filtered = sync_mail_initial(store, ["INBOX"], months=4)
    assert synced == 1
    assert filtered == 0


@patch("giva.sync.mail._fetch_headers_since")
def test_sync_mail_initial_pagination(mock_fetch):
    """Should paginate through all messages in the time window."""
    # First chunk returns 2 messages, second returns empty
    mock_fetch.side_effect = [
        ([{"messageId": f"m{i}", "subject": f"S{i}", "sender": "a@test.com",
           "date": "2026-01-15T10:00:00Z", "toAddrs": [], "ccAddrs": [],
           "isRead": True, "isFlagged": False} for i in range(2)], 4),
        ([{"messageId": f"m{i}", "subject": f"S{i}", "sender": "a@test.com",
           "date": "2026-01-15T10:00:00Z", "toAddrs": [], "ccAddrs": [],
           "isRead": True, "isFlagged": False} for i in range(2, 4)], 4),
        ([], 4),
    ]

    store = MagicMock()

    synced, filtered = sync_mail_initial(store, ["INBOX"], months=4)
    assert synced == 4


@patch("giva.sync.mail._fetch_headers_since")
def test_sync_mail_initial_error_handling(mock_fetch):
    """Should handle errors per-mailbox without crashing."""
    mock_fetch.side_effect = Exception("JXA timeout")

    store = MagicMock()

    synced, filtered = sync_mail_initial(store, ["INBOX", "Sent"], months=4)
    # Should return 0 for both since all mailboxes failed
    assert synced == 0
    assert filtered == 0
    # Should still record error state
    assert store.update_sync_state.called


@patch("giva.sync.mail._fetch_headers_since")
def test_sync_mail_initial_progress_callback(mock_fetch):
    """Should call progress callback during sync."""
    mock_fetch.return_value = (
        [{"messageId": "m1", "subject": "T", "sender": "a@test.com",
          "date": "2026-01-15T10:00:00Z", "toAddrs": [], "ccAddrs": [],
          "isRead": True, "isFlagged": False}],
        1,
    )

    store = MagicMock()
    progress_calls = []

    def on_progress(synced, filtered, total):
        progress_calls.append((synced, filtered, total))

    sync_mail_initial(store, ["INBOX"], months=4, on_progress=on_progress)
    assert len(progress_calls) > 0


# --- fetch_sent_bodies_sample ---


def test_fetch_sent_bodies_sample_with_bodies(tmp_db):
    """Should return sent email samples with bodies."""
    from giva.db.models import Email

    for i in range(5):
        tmp_db.upsert_email(Email(
            message_id=f"sent-sample-{i}@test",
            folder="Sent Messages",
            from_addr="me@test.com",
            subject=f"Important topic number {i}",
            body_plain=f"This is a substantive email body with enough content to pass the filter {i}. " * 3,
            date_sent=datetime(2026, 2, i + 1),
        ))

    samples = fetch_sent_bodies_sample(tmp_db, sample_size=3)
    assert len(samples) == 3
    assert all("subject" in s for s in samples)
    assert all("body" in s for s in samples)
    assert all(len(s["body"]) > 50 for s in samples)


def test_fetch_sent_bodies_sample_filters_trivial(tmp_db):
    """Should filter out emails with short subjects or bodies."""
    from giva.db.models import Email

    # Short subject (≤3 chars) — should be filtered
    tmp_db.upsert_email(Email(
        message_id="short-subj@test", folder="Sent",
        from_addr="me@test.com", subject="Re:",
        body_plain="This is enough body content to pass the length filter test.",
        date_sent=datetime(2026, 2, 1),
    ))
    # Short body (<50 chars) — should be filtered
    tmp_db.upsert_email(Email(
        message_id="short-body@test", folder="Sent",
        from_addr="me@test.com", subject="Valid subject here",
        body_plain="Too short.",
        date_sent=datetime(2026, 2, 2),
    ))
    # Good email — should be kept
    tmp_db.upsert_email(Email(
        message_id="good@test", folder="Sent",
        from_addr="me@test.com", subject="Valid subject here too",
        body_plain="This is a substantive email body that passes both filters easily. " * 3,
        date_sent=datetime(2026, 2, 3),
    ))

    samples = fetch_sent_bodies_sample(tmp_db, sample_size=10)
    assert len(samples) == 1
    assert samples[0]["subject"] == "Valid subject here too"


def test_fetch_sent_bodies_sample_caps_body_length(tmp_db):
    """Should cap body at 2000 chars."""
    from giva.db.models import Email

    tmp_db.upsert_email(Email(
        message_id="long-body@test", folder="Sent",
        from_addr="me@test.com", subject="Long email message",
        body_plain="x" * 5000,
        date_sent=datetime(2026, 2, 1),
    ))

    samples = fetch_sent_bodies_sample(tmp_db, sample_size=5)
    assert len(samples) == 1
    assert len(samples[0]["body"]) == 2000


def test_fetch_sent_bodies_sample_empty(tmp_db):
    """Should return empty list when no sent emails exist."""
    samples = fetch_sent_bodies_sample(tmp_db, sample_size=10)
    assert samples == []


@patch("giva.sync.mail.fetch_email_body")
def test_fetch_sent_bodies_sample_lazy_fetches(mock_fetch_body, tmp_db):
    """Should lazy-fetch bodies for emails without stored body."""
    from giva.db.models import Email

    tmp_db.upsert_email(Email(
        message_id="no-body@test", folder="Sent Messages",
        from_addr="me@test.com", subject="Email without body",
        body_plain="",  # No body stored
        date_sent=datetime(2026, 2, 1),
    ))

    mock_fetch_body.return_value = "Lazy fetched body content that is long enough to pass the filter. " * 3

    samples = fetch_sent_bodies_sample(tmp_db, sample_size=5)
    assert len(samples) == 1
    assert "Lazy fetched" in samples[0]["body"]
    mock_fetch_body.assert_called_once()
