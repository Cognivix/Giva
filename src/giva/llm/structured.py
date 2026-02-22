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
    continue_interview: bool = True
    interview_complete: bool = False
