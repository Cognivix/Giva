"""Tests for prompt template builders."""

from giva.db.models import UserProfile
from giva.llm.prompts import EMAIL_FILTER_SYSTEM, build_filter_prompt


def test_build_filter_prompt_no_profile(tmp_db):
    """Should return generic prompt when no profile exists."""
    result = build_filter_prompt(tmp_db)
    assert result == EMAIL_FILTER_SYSTEM


def test_build_filter_prompt_no_onboarding(tmp_db):
    """Should return generic prompt when onboarding is not completed."""
    profile = UserProfile(
        email_address="me@test.com",
        profile_data={"onboarding_completed": False},
    )
    tmp_db.upsert_profile(profile)

    result = build_filter_prompt(tmp_db)
    assert result == EMAIL_FILTER_SYSTEM


def test_build_filter_prompt_personalized(tmp_db):
    """Should return personalized prompt with priority rules and context."""
    profile = UserProfile(
        email_address="me@test.com",
        display_name="Alice",
        top_contacts=[
            {"addr": "sarah@acme.com", "name": "Sarah Chen", "count": 50},
            {"addr": "bob@acme.com", "name": "Bob Builder", "count": 30},
        ],
        profile_data={
            "onboarding_completed": True,
            "job_title": "Senior Engineer",
            "company": "Acme Corp",
            "priority_rules": {
                "high_priority": ["client emails", "team updates"],
                "low_priority": ["newsletters", "marketing"],
                "ignore": ["automated CI alerts", "social media digests"],
            },
        },
    )
    tmp_db.upsert_profile(profile)

    result = build_filter_prompt(tmp_db)

    # Should NOT be the generic prompt
    assert result != EMAIL_FILTER_SYSTEM

    # Should contain user context
    assert "Senior Engineer" in result
    assert "Acme Corp" in result

    # Should contain top contacts
    assert "Sarah Chen" in result
    assert "Bob Builder" in result

    # Should contain priority rules
    assert "client emails" in result
    assert "team updates" in result
    assert "newsletters" in result
    assert "marketing" in result
    assert "automated CI alerts" in result
    assert "social media digests" in result

    # Should still contain the safety fallback
    assert "When in doubt, KEEP" in result


def test_build_filter_prompt_empty_priority_rules(tmp_db):
    """Should use fallback defaults when priority lists are empty."""
    profile = UserProfile(
        email_address="me@test.com",
        profile_data={
            "onboarding_completed": True,
            "role": "manager",
            "priority_rules": {},
        },
    )
    tmp_db.upsert_profile(profile)

    result = build_filter_prompt(tmp_db)

    # Should be personalized (not generic)
    assert result != EMAIL_FILTER_SYSTEM
    assert "manager" in result

    # Should contain fallback defaults
    assert "client communications" in result
    assert "marketing newsletters" in result
