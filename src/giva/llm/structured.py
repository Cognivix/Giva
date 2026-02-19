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
