"""Data models for Giva's local store."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Email:
    message_id: str
    folder: str
    from_addr: str
    subject: str
    date_sent: datetime
    from_name: str = ""
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    body_plain: str = ""
    body_html: str = ""
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)
    in_reply_to: str = ""
    references_list: list[str] = field(default_factory=list)
    is_read: bool = False
    is_flagged: bool = False
    id: Optional[int] = None

    def to_row(self) -> dict:
        return {
            "message_id": self.message_id,
            "folder": self.folder,
            "from_addr": self.from_addr,
            "from_name": self.from_name,
            "to_addrs": json.dumps(self.to_addrs),
            "cc_addrs": json.dumps(self.cc_addrs),
            "subject": self.subject,
            "date_sent": self.date_sent.isoformat(),
            "body_plain": self.body_plain,
            "body_html": self.body_html,
            "has_attachments": int(self.has_attachments),
            "attachment_names": json.dumps(self.attachment_names),
            "in_reply_to": self.in_reply_to,
            "references_list": json.dumps(self.references_list),
            "is_read": int(self.is_read),
            "is_flagged": int(self.is_flagged),
        }

    @classmethod
    def from_row(cls, row: dict) -> Email:
        return cls(
            id=row["id"],
            message_id=row["message_id"],
            folder=row["folder"],
            from_addr=row["from_addr"],
            from_name=row.get("from_name", ""),
            to_addrs=json.loads(row.get("to_addrs", "[]")),
            cc_addrs=json.loads(row.get("cc_addrs", "[]")),
            subject=row["subject"],
            date_sent=datetime.fromisoformat(row["date_sent"]),
            body_plain=row.get("body_plain", ""),
            body_html=row.get("body_html", ""),
            has_attachments=bool(row.get("has_attachments", 0)),
            attachment_names=json.loads(row.get("attachment_names", "[]")),
            in_reply_to=row.get("in_reply_to", ""),
            references_list=json.loads(row.get("references_list", "[]")),
            is_read=bool(row.get("is_read", 0)),
            is_flagged=bool(row.get("is_flagged", 0)),
        )


@dataclass
class Event:
    uid: str
    calendar_name: str
    summary: str
    dtstart: datetime
    dtend: Optional[datetime] = None
    description: str = ""
    location: str = ""
    all_day: bool = False
    organizer: str = ""
    attendees: list[dict] = field(default_factory=list)
    status: str = "CONFIRMED"
    id: Optional[int] = None

    def to_row(self) -> dict:
        return {
            "uid": self.uid,
            "calendar_name": self.calendar_name,
            "summary": self.summary,
            "description": self.description,
            "location": self.location,
            "dtstart": self.dtstart.isoformat(),
            "dtend": self.dtend.isoformat() if self.dtend else None,
            "all_day": int(self.all_day),
            "organizer": self.organizer,
            "attendees": json.dumps(self.attendees),
            "status": self.status,
        }

    @classmethod
    def from_row(cls, row: dict) -> Event:
        return cls(
            id=row["id"],
            uid=row["uid"],
            calendar_name=row["calendar_name"],
            summary=row["summary"],
            description=row.get("description", ""),
            location=row.get("location", ""),
            dtstart=datetime.fromisoformat(row["dtstart"]),
            dtend=datetime.fromisoformat(row["dtend"]) if row.get("dtend") else None,
            all_day=bool(row.get("all_day", 0)),
            organizer=row.get("organizer", ""),
            attendees=json.loads(row.get("attendees", "[]")),
            status=row.get("status", "CONFIRMED"),
        )


@dataclass
class Task:
    title: str
    source_type: str  # 'email' or 'event'
    source_id: int
    description: str = ""
    priority: str = "medium"  # high, medium, low
    due_date: Optional[datetime] = None
    status: str = "pending"  # pending, in_progress, done, dismissed
    id: Optional[int] = None
    created_at: Optional[datetime] = None


@dataclass
class UserProfile:
    display_name: str = ""
    email_address: str = ""
    top_contacts: list[dict] = field(default_factory=list)
    top_topics: list[str] = field(default_factory=list)
    active_hours: dict[str, int] = field(default_factory=dict)
    avg_response_time_min: float = 0.0
    email_volume_daily: float = 0.0
    profile_data: dict = field(default_factory=dict)
    updated_at: Optional[datetime] = None
    id: int = 1  # Singleton

    def to_row(self) -> dict:
        return {
            "id": 1,
            "display_name": self.display_name,
            "email_address": self.email_address,
            "top_contacts": json.dumps(self.top_contacts),
            "top_topics": json.dumps(self.top_topics),
            "active_hours": json.dumps(self.active_hours),
            "avg_response_time_min": self.avg_response_time_min,
            "email_volume_daily": self.email_volume_daily,
            "profile_data": json.dumps(self.profile_data),
        }

    @classmethod
    def from_row(cls, row: dict) -> UserProfile:
        return cls(
            display_name=row.get("display_name", ""),
            email_address=row.get("email_address", ""),
            top_contacts=json.loads(row.get("top_contacts", "[]")),
            top_topics=json.loads(row.get("top_topics", "[]")),
            active_hours=json.loads(row.get("active_hours", "{}")),
            avg_response_time_min=float(row.get("avg_response_time_min", 0)),
            email_volume_daily=float(row.get("email_volume_daily", 0)),
            profile_data=json.loads(row.get("profile_data", "{}")),
            updated_at=(
                datetime.fromisoformat(row["updated_at"])
                if row.get("updated_at")
                else None
            ),
        )
