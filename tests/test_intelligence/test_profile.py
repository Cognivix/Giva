"""Tests for user profile builder."""

from datetime import datetime

from giva.db.models import Email, UserProfile
from giva.intelligence.profile import (
    _compute_active_hours,
    _compute_avg_response_time,
    _compute_email_volume,
    _compute_top_contacts,
    _detect_user_identity,
    get_profile_summary,
    update_profile,
)


# --- User identity detection ---


def test_detect_identity_from_sent(tmp_db):
    """Should detect user from Sent folder emails."""
    tmp_db.upsert_email(Email(
        message_id="sent-1@test", folder="Sent Messages",
        from_addr="me@example.com", from_name="Alice Smith",
        subject="Re: Hello", date_sent=datetime(2026, 2, 1),
    ))
    tmp_db.upsert_email(Email(
        message_id="sent-2@test", folder="Sent Messages",
        from_addr="me@example.com", from_name="Alice Smith",
        subject="Re: Meeting", date_sent=datetime(2026, 2, 2),
    ))

    addr, name = _detect_user_identity(tmp_db)
    assert addr == "me@example.com"
    assert name == "Alice Smith"


def test_detect_identity_fallback_inbox(tmp_db):
    """Should fallback to INBOX to_addrs if no Sent folder emails."""
    tmp_db.upsert_email(Email(
        message_id="inbox-1@test", folder="INBOX",
        from_addr="bob@example.com", subject="Hey",
        to_addrs=["me@example.com"], date_sent=datetime(2026, 2, 1),
    ))
    tmp_db.upsert_email(Email(
        message_id="inbox-2@test", folder="INBOX",
        from_addr="carol@example.com", subject="Hi",
        to_addrs=["me@example.com"], date_sent=datetime(2026, 2, 2),
    ))

    addr, name = _detect_user_identity(tmp_db)
    assert addr == "me@example.com"


def test_detect_identity_empty(tmp_db):
    """Should return empty strings with no emails."""
    addr, name = _detect_user_identity(tmp_db)
    assert addr == ""
    assert name == ""


# --- Top contacts ---


def test_top_contacts_excludes_self(tmp_db):
    """Top contacts should exclude the user's own email."""
    for i in range(5):
        tmp_db.upsert_email(Email(
            message_id=f"contact-{i}@test", folder="INBOX",
            from_addr="alice@example.com", from_name="Alice",
            subject=f"Email {i}", date_sent=datetime(2026, 2, i + 1),
        ))
    # Also add some from self (should be excluded)
    tmp_db.upsert_email(Email(
        message_id="self@test", folder="INBOX",
        from_addr="me@example.com", from_name="Me",
        subject="Self", date_sent=datetime(2026, 2, 1),
    ))

    contacts = _compute_top_contacts(tmp_db, exclude_addr="me@example.com")
    assert len(contacts) == 1
    assert contacts[0]["addr"] == "alice@example.com"
    assert contacts[0]["count"] == 5


def test_top_contacts_limit(tmp_db):
    """Should respect limit parameter."""
    for i in range(5):
        tmp_db.upsert_email(Email(
            message_id=f"lim-{i}@test", folder="INBOX",
            from_addr=f"user{i}@example.com", from_name=f"User {i}",
            subject=f"Email {i}", date_sent=datetime(2026, 2, i + 1),
        ))

    contacts = _compute_top_contacts(tmp_db, limit=3)
    assert len(contacts) == 3


# --- Active hours ---


def test_active_hours_format(tmp_db):
    """Active hours should be a dict mapping hour strings to counts."""
    tmp_db.upsert_email(Email(
        message_id="hour-1@test", folder="INBOX",
        from_addr="a@test.com", subject="Morning",
        date_sent=datetime(2026, 2, 1, 9, 30),
    ))
    tmp_db.upsert_email(Email(
        message_id="hour-2@test", folder="INBOX",
        from_addr="b@test.com", subject="Afternoon",
        date_sent=datetime(2026, 2, 1, 14, 0),
    ))

    hours = _compute_active_hours(tmp_db)
    assert isinstance(hours, dict)
    assert "9" in hours
    assert "14" in hours
    assert hours["9"] == 1
    assert hours["14"] == 1


def test_active_hours_empty(tmp_db):
    """Should return empty dict with no emails."""
    hours = _compute_active_hours(tmp_db)
    assert hours == {}


# --- Email volume ---


def test_email_volume_positive(tmp_db):
    """Volume should be positive with multiple emails across days."""
    tmp_db.upsert_email(Email(
        message_id="vol-1@test", folder="INBOX",
        from_addr="a@test.com", subject="Day 1",
        date_sent=datetime(2026, 2, 1),
    ))
    tmp_db.upsert_email(Email(
        message_id="vol-2@test", folder="INBOX",
        from_addr="b@test.com", subject="Day 2",
        date_sent=datetime(2026, 2, 3),
    ))

    vol = _compute_email_volume(tmp_db)
    assert vol > 0


def test_email_volume_empty(tmp_db):
    """Should return 0 with no emails."""
    vol = _compute_email_volume(tmp_db)
    assert vol == 0.0


# --- Average response time ---


def test_avg_response_time_with_replies(tmp_db):
    """Should compute response time from reply chains."""
    # Incoming email
    tmp_db.upsert_email(Email(
        message_id="original@test", folder="INBOX",
        from_addr="bob@example.com", subject="Question",
        date_sent=datetime(2026, 2, 1, 10, 0),
    ))
    # Reply from user (30 min later)
    tmp_db.upsert_email(Email(
        message_id="reply@test", folder="Sent Messages",
        from_addr="me@example.com", subject="Re: Question",
        date_sent=datetime(2026, 2, 1, 10, 30),
        in_reply_to="original@test",
    ))

    avg = _compute_avg_response_time(tmp_db)
    assert abs(avg - 30.0) < 1.0  # ~30 minutes


def test_avg_response_time_no_replies(tmp_db):
    """Should return 0 with no reply chains."""
    avg = _compute_avg_response_time(tmp_db)
    assert avg == 0.0


# --- Store Profile CRUD ---


def test_store_profile_crud(tmp_db):
    """Should upsert and retrieve profile."""
    profile = UserProfile(
        display_name="Alice Smith",
        email_address="alice@example.com",
        top_contacts=[{"addr": "bob@test.com", "name": "Bob", "count": 5}],
        top_topics=["budgets", "meetings"],
        active_hours={"9": 10, "14": 8},
        avg_response_time_min=25.0,
        email_volume_daily=12.5,
    )
    tmp_db.upsert_profile(profile)

    retrieved = tmp_db.get_profile()
    assert retrieved is not None
    assert retrieved.display_name == "Alice Smith"
    assert retrieved.email_address == "alice@example.com"
    assert len(retrieved.top_contacts) == 1
    assert retrieved.top_contacts[0]["name"] == "Bob"
    assert retrieved.top_topics == ["budgets", "meetings"]
    assert retrieved.active_hours == {"9": 10, "14": 8}
    assert retrieved.avg_response_time_min == 25.0
    assert retrieved.email_volume_daily == 12.5


def test_store_profile_upsert_updates(tmp_db):
    """Should update profile on second upsert."""
    profile1 = UserProfile(display_name="Alice", email_address="a@test.com")
    tmp_db.upsert_profile(profile1)

    profile2 = UserProfile(display_name="Alice Updated", email_address="a@test.com")
    tmp_db.upsert_profile(profile2)

    retrieved = tmp_db.get_profile()
    assert retrieved.display_name == "Alice Updated"


def test_store_profile_empty(tmp_db):
    """Should return None when no profile exists."""
    assert tmp_db.get_profile() is None


# --- Profile summary formatting ---


def test_profile_summary_with_data(tmp_db):
    """Should produce formatted text summary."""
    profile = UserProfile(
        display_name="Alice Smith",
        email_address="alice@example.com",
        top_contacts=[{"addr": "bob@test.com", "name": "Bob", "count": 5}],
        top_topics=["budgets", "project planning"],
        active_hours={"9": 10, "14": 8, "10": 7},
        avg_response_time_min=25.0,
        email_volume_daily=12.5,
    )
    tmp_db.upsert_profile(profile)

    summary = get_profile_summary(tmp_db)
    assert "Alice Smith" in summary
    assert "alice@example.com" in summary
    assert "Bob" in summary
    assert "budgets" in summary
    assert "12.5 emails/day" in summary


def test_profile_summary_empty_returns_blank(tmp_db):
    """Should return empty string when no profile exists."""
    summary = get_profile_summary(tmp_db)
    assert summary == ""


def test_profile_summary_no_email_returns_blank(tmp_db):
    """Should return empty string when profile has no email_address."""
    profile = UserProfile(display_name="Alice")
    tmp_db.upsert_profile(profile)

    summary = get_profile_summary(tmp_db)
    assert summary == ""


# --- Full update_profile (SQL-only, no LLM) ---


def test_update_profile_full(tmp_db):
    """Full profile update from email data (no LLM topics)."""
    # Seed sent emails (for identity detection)
    tmp_db.upsert_email(Email(
        message_id="sent-up@test", folder="Sent Messages",
        from_addr="alice@work.com", from_name="Alice Worker",
        subject="Re: Project update", date_sent=datetime(2026, 2, 1, 10, 0),
    ))
    # Seed inbox emails (for contacts, hours, volume)
    for i in range(5):
        tmp_db.upsert_email(Email(
            message_id=f"inbox-up-{i}@test", folder="INBOX",
            from_addr="bob@work.com", from_name="Bob Builder",
            subject=f"Topic {i}", date_sent=datetime(2026, 2, i + 1, 9, 30),
        ))

    profile = update_profile(tmp_db)  # No config → no LLM topics
    assert profile.email_address == "alice@work.com"
    assert profile.display_name == "Alice Worker"
    assert len(profile.top_contacts) > 0
    assert profile.email_volume_daily > 0

    # Verify persisted
    stored = tmp_db.get_profile()
    assert stored is not None
    assert stored.email_address == "alice@work.com"
