"""Tests for the onboarding interview module."""

from datetime import datetime

from giva.db.models import Email, Event, UserProfile
from giva.intelligence.onboarding import (
    _filter_visible_token,
    _gather_observations,
    _parse_json,
    is_onboarding_needed,
)


# --- is_onboarding_needed ---


def test_onboarding_needed_no_data(tmp_db):
    """Should return False when there's no synced data."""
    assert is_onboarding_needed(tmp_db) is False


def test_onboarding_needed_with_data_no_profile(tmp_db):
    """Should return True when there's data but no profile."""
    tmp_db.upsert_email(Email(
        message_id="test@test", folder="INBOX",
        from_addr="a@test.com", subject="Hello",
        date_sent=datetime(2026, 2, 1),
    ))
    assert is_onboarding_needed(tmp_db) is True


def test_onboarding_not_needed_when_completed(tmp_db):
    """Should return False when onboarding is already completed."""
    tmp_db.upsert_email(Email(
        message_id="test@test", folder="INBOX",
        from_addr="a@test.com", subject="Hello",
        date_sent=datetime(2026, 2, 1),
    ))
    profile = UserProfile(
        email_address="me@test.com",
        profile_data={"onboarding_completed": True},
    )
    tmp_db.upsert_profile(profile)
    assert is_onboarding_needed(tmp_db) is False


def test_onboarding_needed_when_incomplete(tmp_db):
    """Should return True when onboarding started but not completed."""
    tmp_db.upsert_email(Email(
        message_id="test@test", folder="INBOX",
        from_addr="a@test.com", subject="Hello",
        date_sent=datetime(2026, 2, 1),
    ))
    profile = UserProfile(
        email_address="me@test.com",
        profile_data={"onboarding_completed": False, "onboarding_step": 1},
    )
    tmp_db.upsert_profile(profile)
    assert is_onboarding_needed(tmp_db) is True


# --- _gather_observations ---


def test_gather_observations_with_data(tmp_db):
    """Should build observations from email data."""
    # Add sent email for identity detection
    tmp_db.upsert_email(Email(
        message_id="sent@test", folder="Sent Messages",
        from_addr="me@test.com", from_name="Test User",
        subject="Re: Hello", date_sent=datetime(2026, 2, 1, 10, 0),
    ))
    # Add inbox emails
    for i in range(3):
        tmp_db.upsert_email(Email(
            message_id=f"inbox-{i}@test", folder="INBOX",
            from_addr="alice@test.com", from_name="Alice",
            subject=f"Topic {i}", date_sent=datetime(2026, 2, i + 1, 9, 30),
        ))

    obs = _gather_observations(tmp_db)
    assert "me@test.com" in obs
    assert "Alice" in obs
    assert "emails" in obs.lower()


def test_gather_observations_empty(tmp_db):
    """Should return minimal text with no data."""
    obs = _gather_observations(tmp_db)
    assert "Total:" in obs


def test_gather_observations_includes_events(tmp_db):
    """Should include upcoming events in observations."""
    from datetime import timedelta

    # Use a date that's definitely within the next 7 days
    tomorrow = datetime.now() + timedelta(days=1)
    tmp_db.upsert_event(Event(
        uid="evt-1", calendar_name="Work",
        summary="Team standup",
        dtstart=tomorrow.replace(hour=10, minute=0),
        dtend=tomorrow.replace(hour=10, minute=30),
    ))

    obs = _gather_observations(tmp_db)
    assert "Team standup" in obs


# --- _filter_visible_token ---


def test_filter_no_tag():
    """Should yield all text when no tag present."""
    tokens = ["Hello", " world"]
    visible, done = _filter_visible_token(tokens, [])
    assert visible == "Hello world"
    assert done is False


def test_filter_hides_tag():
    """Should stop yielding when profile_update tag is found."""
    tokens = ["Great!", " <profile_update>{\"role\": \"engineer\"}</profile_update>"]
    visible, done = _filter_visible_token(tokens, [])
    assert visible == "Great! "
    assert done is False


def test_filter_done_when_tag_closes():
    """Should signal done when closing tag is found."""
    # First call yields "Hi"
    visible, done = _filter_visible_token(["Hi"], [])
    assert visible == "Hi"
    assert done is False

    # With full text including closing tag
    all_tokens = ["Hi<profile_update>{}</profile_update>"]
    visible2, done2 = _filter_visible_token(all_tokens, ["Hi"])
    assert done2 is True


def test_filter_inside_tag_no_close():
    """Should not yield anything once inside tag but before close."""
    # Full open tag is present but no closing tag yet
    tokens = ["Text<profile_update>{\"role\":"]
    visible, done = _filter_visible_token(tokens, ["Text"])
    # We're past the visible portion (before tag), inside the tag now
    assert visible is None
    assert done is False


# --- _parse_json ---


def test_parse_json_direct():
    """Should parse direct JSON."""
    result = _parse_json('{"role": "engineer"}')
    assert result == {"role": "engineer"}


def test_parse_json_markdown_fenced():
    """Should parse JSON from markdown code blocks."""
    text = '```json\n{"role": "engineer"}\n```'
    result = _parse_json(text)
    assert result == {"role": "engineer"}


def test_parse_json_embedded():
    """Should extract JSON from surrounding text."""
    text = 'Some text {"role": "engineer"} more text'
    result = _parse_json(text)
    assert result == {"role": "engineer"}


def test_parse_json_invalid():
    """Should return None for unparseable text."""
    result = _parse_json("not json at all")
    assert result is None


def test_parse_json_array_returns_none():
    """Should return None for arrays (not dicts)."""
    result = _parse_json('["a", "b"]')
    assert result is None


# --- Store methods: update_profile_data and reset_all_data ---


def test_update_profile_data_creates_profile(tmp_db):
    """update_profile_data should create a profile if none exists."""
    tmp_db.update_profile_data({"role": "engineer", "company": "Acme"})

    profile = tmp_db.get_profile()
    assert profile is not None
    assert profile.profile_data["role"] == "engineer"
    assert profile.profile_data["company"] == "Acme"


def test_update_profile_data_merges(tmp_db):
    """update_profile_data should merge without overwriting existing keys."""
    tmp_db.update_profile_data({"role": "engineer"})
    tmp_db.update_profile_data({"company": "Acme"})

    profile = tmp_db.get_profile()
    assert profile.profile_data["role"] == "engineer"
    assert profile.profile_data["company"] == "Acme"


def test_update_profile_data_overwrites_key(tmp_db):
    """update_profile_data should overwrite an existing key."""
    tmp_db.update_profile_data({"role": "engineer"})
    tmp_db.update_profile_data({"role": "manager"})

    profile = tmp_db.get_profile()
    assert profile.profile_data["role"] == "manager"


def test_update_profile_data_preserves_analytics(tmp_db):
    """update_profile_data should not touch analytics fields."""
    profile = UserProfile(
        display_name="Alice",
        email_address="alice@test.com",
        avg_response_time_min=25.0,
    )
    tmp_db.upsert_profile(profile)
    tmp_db.update_profile_data({"role": "engineer"})

    updated = tmp_db.get_profile()
    assert updated.display_name == "Alice"
    assert updated.email_address == "alice@test.com"
    assert updated.avg_response_time_min == 25.0
    assert updated.profile_data["role"] == "engineer"


def test_reset_all_data(tmp_db):
    """reset_all_data should clear all tables."""
    # Seed data
    tmp_db.upsert_email(Email(
        message_id="reset@test", folder="INBOX",
        from_addr="a@test.com", subject="Hello",
        date_sent=datetime(2026, 2, 1),
    ))
    tmp_db.upsert_event(Event(
        uid="evt-reset", calendar_name="Work",
        summary="Meeting", dtstart=datetime(2026, 3, 1),
    ))
    tmp_db.upsert_profile(UserProfile(
        display_name="Alice", email_address="a@test.com",
    ))
    tmp_db.add_message("user", "Hello")

    stats = tmp_db.get_stats()
    assert stats["emails"] > 0
    assert stats["events"] > 0

    tmp_db.reset_all_data()

    stats = tmp_db.get_stats()
    assert stats["emails"] == 0
    assert stats["events"] == 0
    assert tmp_db.get_profile() is None
    assert tmp_db.get_recent_messages() == []


# --- Profile preservation across update_profile ---


def test_update_profile_preserves_profile_data(tmp_db):
    """update_profile() should preserve profile_data from onboarding."""
    # Seed emails for identity detection
    tmp_db.upsert_email(Email(
        message_id="sent@test", folder="Sent Messages",
        from_addr="alice@test.com", from_name="Alice",
        subject="Re: Hello", date_sent=datetime(2026, 2, 1),
    ))
    for i in range(3):
        tmp_db.upsert_email(Email(
            message_id=f"inbox-{i}@test", folder="INBOX",
            from_addr="bob@test.com", from_name="Bob",
            subject=f"Email {i}", date_sent=datetime(2026, 2, i + 1),
        ))

    # Set onboarding data
    profile = UserProfile(
        email_address="alice@test.com",
        display_name="Alice",
        profile_data={"onboarding_completed": True, "role": "engineer"},
    )
    tmp_db.upsert_profile(profile)

    # Run profile update (no LLM, no config)
    from giva.intelligence.profile import update_profile

    updated = update_profile(tmp_db)

    # profile_data should be preserved
    assert updated.profile_data.get("onboarding_completed") is True
    assert updated.profile_data.get("role") == "engineer"


# --- Profile summary includes onboarding data ---


def test_profile_summary_includes_onboarding_fields(tmp_db):
    """Profile summary should include rich onboarding fields."""
    from giva.intelligence.profile import get_profile_summary

    profile = UserProfile(
        display_name="Alice Smith",
        email_address="alice@test.com",
        profile_data={
            "onboarding_completed": True,
            "role": "software engineer",
            "job_title": "Senior Engineer",
            "company": "Acme Corp",
            "department": "Platform",
            "communication_style": "brief and direct",
            "priority_rules": {
                "high_priority": ["client emails", "team updates"],
                "low_priority": ["newsletters"],
                "ignore": ["marketing spam"],
            },
            "work_schedule": {
                "start_hour": 9,
                "end_hour": 17,
            },
        },
    )
    tmp_db.upsert_profile(profile)

    summary = get_profile_summary(tmp_db)
    assert "software engineer" in summary
    assert "Senior Engineer" in summary
    assert "Acme Corp" in summary
    assert "Platform" in summary
    assert "brief and direct" in summary
    assert "client emails" in summary
    assert "newsletters" in summary
    assert "marketing spam" in summary
    assert "9:00 - 17:00" in summary
