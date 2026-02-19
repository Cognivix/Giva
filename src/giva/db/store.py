"""SQLite data access layer with FTS5 full-text search."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from giva.db.models import Email, Event, Task, UserProfile

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE NOT NULL,
    folder TEXT NOT NULL,
    from_addr TEXT NOT NULL,
    from_name TEXT DEFAULT '',
    to_addrs TEXT NOT NULL DEFAULT '[]',
    cc_addrs TEXT DEFAULT '[]',
    subject TEXT NOT NULL,
    date_sent TEXT NOT NULL,
    body_plain TEXT DEFAULT '',
    body_html TEXT DEFAULT '',
    has_attachments INTEGER NOT NULL DEFAULT 0,
    attachment_names TEXT DEFAULT '[]',
    in_reply_to TEXT DEFAULT '',
    references_list TEXT DEFAULT '[]',
    is_read INTEGER NOT NULL DEFAULT 0,
    is_flagged INTEGER NOT NULL DEFAULT 0,
    synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, body_plain, from_name, from_addr,
    content='emails', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, body_plain, from_name, from_addr)
    VALUES (new.id, new.subject, new.body_plain, new.from_name, new.from_addr);
END;

CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body_plain, from_name, from_addr)
    VALUES ('delete', old.id, old.subject, old.body_plain, old.from_name, old.from_addr);
END;

CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body_plain, from_name, from_addr)
    VALUES ('delete', old.id, old.subject, old.body_plain, old.from_name, old.from_addr);
    INSERT INTO emails_fts(rowid, subject, body_plain, from_name, from_addr)
    VALUES (new.id, new.subject, new.body_plain, new.from_name, new.from_addr);
END;

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE NOT NULL,
    calendar_name TEXT NOT NULL,
    summary TEXT NOT NULL,
    description TEXT DEFAULT '',
    location TEXT DEFAULT '',
    dtstart TEXT NOT NULL,
    dtend TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    organizer TEXT DEFAULT '',
    attendees TEXT DEFAULT '[]',
    status TEXT DEFAULT 'CONFIRMED',
    synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    priority TEXT CHECK(priority IN ('high', 'medium', 'low')) DEFAULT 'medium',
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'in_progress', 'done', 'dismissed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_extraction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    extracted_at TEXT NOT NULL DEFAULT (datetime('now')),
    task_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_type, source_id)
);

CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    display_name TEXT DEFAULT '',
    email_address TEXT DEFAULT '',
    top_contacts TEXT DEFAULT '[]',
    top_topics TEXT DEFAULT '[]',
    active_hours TEXT DEFAULT '{}',
    avg_response_time_min REAL DEFAULT 0,
    email_volume_daily REAL DEFAULT 0,
    profile_data TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL UNIQUE,
    last_sync TEXT,
    last_count INTEGER DEFAULT 0,
    last_status TEXT DEFAULT 'never'
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Store:
    """SQLite data store for Giva."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            # Record schema version if not present
            existing = conn.execute(
                "SELECT version FROM schema_version WHERE version = ?", (SCHEMA_VERSION,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- Emails ---

    def upsert_email(self, email: Email) -> int:
        row = email.to_row()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO emails
                    (message_id, folder, from_addr, from_name, to_addrs, cc_addrs,
                     subject, date_sent, body_plain, body_html, has_attachments,
                     attachment_names, in_reply_to, references_list, is_read, is_flagged)
                VALUES
                    (:message_id, :folder, :from_addr, :from_name, :to_addrs, :cc_addrs,
                     :subject, :date_sent, :body_plain, :body_html, :has_attachments,
                     :attachment_names, :in_reply_to, :references_list, :is_read, :is_flagged)
                ON CONFLICT(message_id) DO UPDATE SET
                    is_read = excluded.is_read,
                    is_flagged = excluded.is_flagged,
                    synced_at = datetime('now')
                """,
                row,
            )
            cursor = conn.execute(
                "SELECT id FROM emails WHERE message_id = ?", (email.message_id,)
            )
            return cursor.fetchone()["id"]

    def search_emails(self, query: str, limit: int = 20) -> list[Email]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT e.* FROM emails e
                   JOIN emails_fts f ON e.id = f.rowid
                   WHERE emails_fts MATCH ?
                   ORDER BY e.date_sent DESC
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            return [Email.from_row(dict(r)) for r in rows]

    def get_recent_emails(self, limit: int = 20, folder: Optional[str] = None) -> list[Email]:
        with self._conn() as conn:
            if folder:
                rows = conn.execute(
                    "SELECT * FROM emails WHERE folder = ? ORDER BY date_sent DESC LIMIT ?",
                    (folder, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM emails ORDER BY date_sent DESC LIMIT ?", (limit,)
                ).fetchall()
            return [Email.from_row(dict(r)) for r in rows]

    def get_emails_since(self, since: datetime, folder: Optional[str] = None) -> list[Email]:
        with self._conn() as conn:
            if folder:
                rows = conn.execute(
                    "SELECT * FROM emails WHERE date_sent >= ? AND folder = ? ORDER BY date_sent DESC",
                    (since.isoformat(), folder),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM emails WHERE date_sent >= ? ORDER BY date_sent DESC",
                    (since.isoformat(),),
                ).fetchall()
            return [Email.from_row(dict(r)) for r in rows]

    def get_emails_from(self, sender: str, limit: int = 20) -> list[Email]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM emails WHERE from_addr LIKE ? OR from_name LIKE ? ORDER BY date_sent DESC LIMIT ?",
                (f"%{sender}%", f"%{sender}%", limit),
            ).fetchall()
            return [Email.from_row(dict(r)) for r in rows]

    def update_email_body(self, message_id: str, body_plain: str) -> None:
        """Cache a lazily-fetched email body."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE emails SET body_plain = ? WHERE message_id = ?",
                (body_plain, message_id),
            )

    def email_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) as c FROM emails").fetchone()["c"]

    # --- Events ---

    def upsert_event(self, event: Event) -> int:
        row = event.to_row()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO events
                    (uid, calendar_name, summary, description, location,
                     dtstart, dtend, all_day, organizer, attendees, status)
                VALUES
                    (:uid, :calendar_name, :summary, :description, :location,
                     :dtstart, :dtend, :all_day, :organizer, :attendees, :status)
                ON CONFLICT(uid) DO UPDATE SET
                    summary = excluded.summary,
                    description = excluded.description,
                    location = excluded.location,
                    dtstart = excluded.dtstart,
                    dtend = excluded.dtend,
                    attendees = excluded.attendees,
                    status = excluded.status,
                    synced_at = datetime('now')
                """,
                row,
            )
            cursor = conn.execute("SELECT id FROM events WHERE uid = ?", (event.uid,))
            return cursor.fetchone()["id"]

    def get_upcoming_events(self, days: int = 7) -> list[Event]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM events
                   WHERE dtstart >= datetime('now', 'localtime')
                     AND dtstart <= datetime('now', 'localtime', '+' || ? || ' days')
                   ORDER BY dtstart ASC""",
                (days,),
            ).fetchall()
            return [Event.from_row(dict(r)) for r in rows]

    def get_events_range(self, start: datetime, end: datetime) -> list[Event]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE dtstart >= ? AND dtstart <= ? ORDER BY dtstart ASC",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            return [Event.from_row(dict(r)) for r in rows]

    def event_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]

    # --- Tasks ---

    def add_task(self, task: Task) -> int:
        """Insert a new task. Returns the task ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO tasks (title, description, source_type, source_id,
                                      priority, due_date, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.title,
                    task.description,
                    task.source_type,
                    task.source_id,
                    task.priority,
                    task.due_date.isoformat() if task.due_date else None,
                    task.status,
                ),
            )
            return cursor.lastrowid

    def get_tasks(self, status: Optional[str] = None, limit: int = 50) -> list[Task]:
        """Get tasks, optionally filtered by status. Ordered by priority then date."""
        with self._conn() as conn:
            order = (
                "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                "created_at DESC"
            )
            if status:
                rows = conn.execute(
                    f"SELECT * FROM tasks WHERE status = ? ORDER BY {order} LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM tasks ORDER BY {order} LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._task_from_row(dict(r)) for r in rows]

    def get_task(self, task_id: int) -> Optional[Task]:
        """Get a single task by ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._task_from_row(dict(row)) if row else None

    def update_task_status(self, task_id: int, status: str) -> bool:
        """Update a task's status. Returns True if the task was found."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status, task_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _task_from_row(row: dict) -> Task:
        return Task(
            id=row["id"],
            title=row["title"],
            description=row.get("description", ""),
            source_type=row["source_type"],
            source_id=row["source_id"],
            priority=row.get("priority", "medium"),
            due_date=datetime.fromisoformat(row["due_date"]) if row.get("due_date") else None,
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
        )

    # --- Task Extraction Tracking ---

    def get_unprocessed_email_ids(self, limit: int = 50) -> list[int]:
        """Get email IDs not yet processed for task extraction."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT e.id FROM emails e
                   LEFT JOIN task_extraction_log l
                       ON l.source_type = 'email' AND l.source_id = e.id
                   WHERE l.id IS NULL
                   ORDER BY e.date_sent DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [r["id"] for r in rows]

    def get_unprocessed_event_ids(self, limit: int = 50) -> list[int]:
        """Get event IDs not yet processed for task extraction."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT e.id FROM events e
                   LEFT JOIN task_extraction_log l
                       ON l.source_type = 'event' AND l.source_id = e.id
                   WHERE l.id IS NULL
                   ORDER BY e.dtstart DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [r["id"] for r in rows]

    def mark_extracted(self, source_type: str, source_id: int, task_count: int) -> None:
        """Record that a source item has been processed for task extraction."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO task_extraction_log
                   (source_type, source_id, task_count) VALUES (?, ?, ?)""",
                (source_type, source_id, task_count),
            )

    def get_email_by_id(self, email_id: int) -> Optional[Email]:
        """Get a single email by its row ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
            return Email.from_row(dict(row)) if row else None

    def get_event_by_id(self, event_id: int) -> Optional[Event]:
        """Get a single event by its row ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return Event.from_row(dict(row)) if row else None

    # --- Sync State ---

    def get_sync_state(self, source: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sync_state WHERE source = ?", (source,)
            ).fetchone()
            return dict(row) if row else None

    def update_sync_state(self, source: str, count: int, status: str = "success"):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO sync_state (source, last_sync, last_count, last_status)
                   VALUES (?, datetime('now'), ?, ?)
                   ON CONFLICT(source) DO UPDATE SET
                       last_sync = datetime('now'),
                       last_count = excluded.last_count,
                       last_status = excluded.last_status""",
                (source, count, status),
            )

    # --- Conversations ---

    def add_message(self, role: str, content: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations (role, content) VALUES (?, ?)", (role, content)
            )

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # --- User Profile ---

    def get_profile(self) -> Optional[UserProfile]:
        """Get the singleton user profile, or None if not yet built."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_profile WHERE id = 1"
            ).fetchone()
            return UserProfile.from_row(dict(row)) if row else None

    def upsert_profile(self, profile: UserProfile) -> None:
        """Create or update the singleton user profile."""
        row = profile.to_row()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_profile
                    (id, display_name, email_address, top_contacts, top_topics,
                     active_hours, avg_response_time_min, email_volume_daily,
                     profile_data, updated_at)
                VALUES
                    (:id, :display_name, :email_address, :top_contacts, :top_topics,
                     :active_hours, :avg_response_time_min, :email_volume_daily,
                     :profile_data, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    email_address = excluded.email_address,
                    top_contacts = excluded.top_contacts,
                    top_topics = excluded.top_topics,
                    active_hours = excluded.active_hours,
                    avg_response_time_min = excluded.avg_response_time_min,
                    email_volume_daily = excluded.email_volume_daily,
                    profile_data = excluded.profile_data,
                    updated_at = datetime('now')
                """,
                row,
            )

    # --- Stats ---

    def get_stats(self) -> dict:
        with self._conn() as conn:
            emails = conn.execute("SELECT COUNT(*) as c FROM emails").fetchone()["c"]
            events = conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
            tasks = conn.execute(
                "SELECT COUNT(*) as c FROM tasks WHERE status = 'pending'"
            ).fetchone()["c"]
            syncs = conn.execute(
                "SELECT source, last_sync, last_count, last_status FROM sync_state"
            ).fetchall()
            return {
                "emails": emails,
                "events": events,
                "pending_tasks": tasks,
                "syncs": [dict(s) for s in syncs],
            }
