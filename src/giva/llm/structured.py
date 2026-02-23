"""Pydantic models for structured LLM output."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Priority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class ExtractedTask(BaseModel):
    title: str = Field(description="Concise task title")
    description: Optional[str] = Field(default=None, description="Additional context")
    priority: Priority = Field(default=Priority.medium)
    due_date: Optional[str] = Field(default=None, description="ISO 8601 date if mentioned")
    source_quote: str = Field(default="", description="Relevant quote from source")


class TaskExtractionResult(BaseModel):
    tasks: list[ExtractedTask] = Field(default_factory=list)
    has_actionable_items: bool = False


# --- Onboarding profile models ---


class PriorityRules(BaseModel):
    high_priority: list[str] = Field(default_factory=list)
    low_priority: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)


class WorkSchedule(BaseModel):
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None


class OnboardingProfileUpdate(BaseModel):
    role: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    company: Optional[str] = None
    personality_notes: Optional[str] = None
    communication_style: Optional[str] = None
    priority_rules: Optional[PriorityRules] = None
    work_schedule: Optional[WorkSchedule] = None
    preferences: list[str] = Field(default_factory=list)
    initial_goals: list[dict] = Field(default_factory=list)
    continue_interview: bool = True
    interview_complete: bool = False


# --- Goal & strategy models ---


class InferredGoal(BaseModel):
    """A goal inferred from user profile, emails, or conversation."""

    title: str = Field(description="Concise goal title")
    tier: str = Field(description="long_term or mid_term")
    category: str = Field(default="", description="career, personal, health, etc.")
    description: Optional[str] = None
    priority: Priority = Priority.medium
    target_date: Optional[str] = Field(default=None, description="ISO date if estimable")


class GoalInferenceResult(BaseModel):
    """Result of goal inference from user data."""

    goals: list[InferredGoal] = Field(default_factory=list)
    reasoning: str = Field(default="", description="Brief explanation of inferred goals")


class StrategyResult(BaseModel):
    """LLM-generated strategy for achieving a goal."""

    approach: str = Field(description="Overall strategic approach (1-2 sentences)")
    action_items: list[dict] = Field(default_factory=list)
    suggested_objectives: list[InferredGoal] = Field(default_factory=list)


class TacticalPlan(BaseModel):
    """Concrete actions to advance a mid-term objective."""

    tasks: list[ExtractedTask] = Field(default_factory=list)
    email_drafts: list[dict] = Field(default_factory=list)
    calendar_blocks: list[dict] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


class DailyReviewResult(BaseModel):
    """Structured daily review output."""

    summary: str = Field(description="Summary of the day's progress")
    goal_updates: list[dict] = Field(default_factory=list)
    suggested_focus: list[str] = Field(default_factory=list)
