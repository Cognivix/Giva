"""SQLite data access layer with FTS5 full-text search."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from giva.db.models import (
    DailyReview,
    Email,
    Event,
    Goal,
    GoalProgress,
    GoalStrategy,
    Task,
    UserProfile,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 6

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
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    title, description, content='tasks', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, title, description)
    VALUES (new.id, new.title, new.description);
END;

CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, title, description)
    VALUES ('delete', old.id, old.title, old.description);
END;

CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, title, description)
    VALUES ('delete', old.id, old.title, old.description);
    INSERT INTO tasks_fts(rowid, title, description)
    VALUES (new.id, new.title, new.description);
END;

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
    goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    tier TEXT NOT NULL CHECK(tier IN ('long_term', 'mid_term', 'short_term')),
    category TEXT DEFAULT '',
    parent_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'completed', 'abandoned')),
    priority TEXT CHECK(priority IN ('high', 'medium', 'low')) DEFAULT 'medium',
    target_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (parent_id) REFERENCES goals(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS goal_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS goal_strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    strategy_text TEXT NOT NULL,
    action_items TEXT DEFAULT '[]',
    suggested_objectives TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed', 'accepted', 'rejected', 'superseded')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_date TEXT NOT NULL UNIQUE,
    prompt_text TEXT NOT NULL,
    user_response TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    query TEXT NOT NULL,
    params TEXT DEFAULT '{}',
    success INTEGER NOT NULL DEFAULT 1,
    output_summary TEXT DEFAULT '',
    artifacts TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
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
        # Run migrations for existing databases before creating schema
        from giva.db.migrations import migrate

        migrate(self.db_path)

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
                                      priority, due_date, status, goal_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.title,
                    task.description,
                    task.source_type,
                    task.source_id,
                    task.priority,
                    task.due_date.isoformat() if task.due_date else None,
                    task.status,
                    task.goal_id,
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

    def update_task(self, task_id: int, **kwargs) -> bool:
        """Update task fields. Returns True if the task was found.

        Allowed fields: title, description, priority, due_date, status, goal_id.
        """
        allowed = {"title", "description", "priority", "due_date", "status", "goal_id"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return False
        if "due_date" in fields and fields["due_date"] is not None:
            if isinstance(fields["due_date"], datetime):
                fields["due_date"] = fields["due_date"].isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [task_id]
        with self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {sets}, updated_at = datetime('now') WHERE id = ?",
                vals,
            )
            return cursor.rowcount > 0

    def search_tasks(self, query: str, limit: int = 20) -> list[Task]:
        """Search tasks using FTS5 full-text search."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT t.* FROM tasks t
                   JOIN tasks_fts f ON t.id = f.rowid
                   WHERE tasks_fts MATCH ?
                   ORDER BY t.created_at DESC
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            return [self._task_from_row(dict(r)) for r in rows]

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
            goal_id=row.get("goal_id"),
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

    def add_message(
        self,
        role: str,
        content: str,
        goal_id: Optional[int] = None,
        task_id: Optional[int] = None,
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations (role, content, goal_id, task_id)"
                " VALUES (?, ?, ?, ?)",
                (role, content, goal_id, task_id),
            )

    def get_recent_messages(
        self,
        limit: int = 20,
        goal_id: Optional[int] = None,
        task_id: Optional[int] = None,
    ) -> list[dict]:
        """Get recent messages, scoped by goal_id or task_id.

        When both are None, returns only global (non-goal, non-task) messages.
        When goal_id is set, returns only messages for that goal.
        When task_id is set, returns only messages for that task.
        """
        with self._conn() as conn:
            if task_id is not None:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM conversations "
                    "WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            elif goal_id is not None:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM conversations "
                    "WHERE goal_id = ? ORDER BY id DESC LIMIT ?",
                    (goal_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT role, content, created_at FROM conversations "
                    "WHERE goal_id IS NULL AND task_id IS NULL"
                    " ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def get_goal_messages(self, goal_id: int, limit: int = 50) -> list[dict]:
        """Get conversation messages scoped to a specific goal."""
        return self.get_recent_messages(limit=limit, goal_id=goal_id)

    def get_task_messages(self, task_id: int, limit: int = 50) -> list[dict]:
        """Get conversation messages scoped to a specific task."""
        return self.get_recent_messages(limit=limit, task_id=task_id)

    def get_conversation_dates(self, limit: int = 30) -> list[dict]:
        """Get distinct dates with first user message as preview.

        Returns a list of dicts: [{date, preview, message_count}]
        ordered by date descending (most recent first).
        Only includes global chat (not goal-scoped or task-scoped messages).
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    date(created_at) as day,
                    MIN(CASE WHEN role = 'user' THEN content END) as preview,
                    COUNT(*) as message_count
                FROM conversations
                WHERE goal_id IS NULL AND task_id IS NULL
                GROUP BY date(created_at)
                ORDER BY day DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_messages_for_date(
        self, date_str: str, limit: int = 200
    ) -> list[dict]:
        """Get all global messages for a specific date.

        date_str should be in YYYY-MM-DD format.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at
                FROM conversations
                WHERE goal_id IS NULL AND task_id IS NULL
                  AND date(created_at) = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (date_str, limit),
            ).fetchall()
            return [dict(r) for r in rows]

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

    def update_profile_data(self, data: dict) -> None:
        """Merge new keys into the profile_data JSON without touching analytics fields."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT profile_data FROM user_profile WHERE id = 1"
            ).fetchone()
            if row:
                try:
                    existing = json.loads(row["profile_data"] or "{}")
                except (json.JSONDecodeError, TypeError, ValueError):
                    log.warning("Corrupted profile_data JSON, starting fresh")
                    existing = {}
                existing.update(data)
                conn.execute(
                    "UPDATE user_profile SET profile_data = ?, updated_at = datetime('now') "
                    "WHERE id = 1",
                    (json.dumps(existing),),
                )
            else:
                conn.execute(
                    "INSERT INTO user_profile (id, profile_data) VALUES (1, ?)",
                    (json.dumps(data),),
                )

    # --- Reset ---

    def reset_all_data(self) -> None:
        """Clear all user data for a full reset. Preserves the schema.

        If the DB is corrupt (malformed disk image), deletes the file
        and recreates it from scratch.
        """
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM goal_progress")
                conn.execute("DELETE FROM goal_strategies")
                conn.execute("DELETE FROM daily_reviews")
                conn.execute("DELETE FROM goals")
                conn.execute("DELETE FROM emails")
                conn.execute("DELETE FROM events")
                conn.execute("DELETE FROM tasks")
                conn.execute("DELETE FROM conversations")
                conn.execute("DELETE FROM task_extraction_log")
                conn.execute("DELETE FROM sync_state")
                conn.execute("DELETE FROM user_profile")
                conn.execute("DELETE FROM agent_executions")
                # Rebuild FTS indexes after clearing data
                conn.execute("INSERT INTO emails_fts(emails_fts) VALUES('rebuild')")
                conn.execute("INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')")
        except sqlite3.DatabaseError as e:
            log.warning("DB corrupt during reset (%s) — deleting and recreating", e)
            self._nuke_and_recreate()

    def _nuke_and_recreate(self) -> None:
        """Delete a corrupt DB file (+ WAL/SHM) and recreate from schema."""
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.db_path) + suffix)
            try:
                p.unlink(missing_ok=True)
            except OSError as e:
                log.error("Failed to delete %s: %s", p, e)
        log.info("Recreating DB at %s", self.db_path)
        self._init_db()

    # --- Stats ---

    def get_stats(self) -> dict:
        with self._conn() as conn:
            emails = conn.execute("SELECT COUNT(*) as c FROM emails").fetchone()["c"]
            events = conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
            tasks = conn.execute(
                "SELECT COUNT(*) as c FROM tasks WHERE status = 'pending'"
            ).fetchone()["c"]
            active_goals = conn.execute(
                "SELECT COUNT(*) as c FROM goals WHERE status = 'active'"
            ).fetchone()["c"]
            syncs = conn.execute(
                "SELECT source, last_sync, last_count, last_status FROM sync_state"
            ).fetchall()
            return {
                "emails": emails,
                "events": events,
                "pending_tasks": tasks,
                "active_goals": active_goals,
                "syncs": [dict(s) for s in syncs],
            }

    # --- Goals ---

    def add_goal(self, goal: Goal) -> int:
        """Insert a new goal. Returns the goal ID."""
        row = goal.to_row()
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO goals (title, description, tier, category,
                                     parent_id, status, priority, target_date)
                   VALUES (:title, :description, :tier, :category,
                           :parent_id, :status, :priority, :target_date)""",
                row,
            )
            return cursor.lastrowid

    def get_goal(self, goal_id: int) -> Optional[Goal]:
        """Get a single goal by ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            return Goal.from_row(dict(row)) if row else None

    def get_goals(
        self,
        tier: Optional[str] = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[Goal]:
        """Get goals, optionally filtered by tier and status."""
        with self._conn() as conn:
            conditions = []
            params: list = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if tier:
                conditions.append("tier = ?")
                params.append(tier)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            order = (
                "CASE tier WHEN 'long_term' THEN 0 WHEN 'mid_term' THEN 1 ELSE 2 END, "
                "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                "created_at DESC"
            )
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM goals {where} ORDER BY {order} LIMIT ?", params
            ).fetchall()
            return [Goal.from_row(dict(r)) for r in rows]

    def get_child_goals(self, parent_id: int) -> list[Goal]:
        """Get child goals of a parent goal."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE parent_id = ? AND status = 'active' "
                "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END",
                (parent_id,),
            ).fetchall()
            return [Goal.from_row(dict(r)) for r in rows]

    def update_goal(self, goal_id: int, **kwargs) -> bool:
        """Update goal fields. Returns True if goal was found."""
        allowed = {
            "title", "description", "tier", "category", "parent_id",
            "status", "priority", "target_date",
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return False
        if "target_date" in fields and fields["target_date"] is not None:
            if isinstance(fields["target_date"], datetime):
                fields["target_date"] = fields["target_date"].isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [goal_id]
        with self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE goals SET {sets}, updated_at = datetime('now') WHERE id = ?",
                vals,
            )
            return cursor.rowcount > 0

    def update_goal_status(self, goal_id: int, status: str) -> bool:
        """Update a goal's status. Returns True if found."""
        return self.update_goal(goal_id, status=status)

    # --- Goal Progress ---

    def add_goal_progress(self, goal_id: int, note: str, source: str = "user") -> int:
        """Add a progress entry for a goal. Returns the entry ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO goal_progress (goal_id, note, source) VALUES (?, ?, ?)",
                (goal_id, note, source),
            )
            return cursor.lastrowid

    def get_goal_progress(self, goal_id: int, limit: int = 20) -> list[GoalProgress]:
        """Get progress entries for a goal, most recent first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goal_progress WHERE goal_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (goal_id, limit),
            ).fetchall()
            return [GoalProgress.from_row(dict(r)) for r in rows]

    # --- Goal Strategies ---

    def add_strategy(self, strategy: GoalStrategy) -> int:
        """Insert a new strategy. Returns the strategy ID."""
        row = strategy.to_row()
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO goal_strategies
                   (goal_id, strategy_text, action_items, suggested_objectives, status)
                   VALUES (:goal_id, :strategy_text, :action_items,
                           :suggested_objectives, :status)""",
                row,
            )
            return cursor.lastrowid

    def get_strategies(
        self, goal_id: int, status: Optional[str] = None
    ) -> list[GoalStrategy]:
        """Get strategies for a goal."""
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM goal_strategies WHERE goal_id = ? AND status = ? "
                    "ORDER BY created_at DESC",
                    (goal_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM goal_strategies WHERE goal_id = ? "
                    "ORDER BY created_at DESC",
                    (goal_id,),
                ).fetchall()
            return [GoalStrategy.from_row(dict(r)) for r in rows]

    def update_strategy_status(self, strategy_id: int, status: str) -> bool:
        """Update a strategy's status. Returns True if found."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE goal_strategies SET status = ? WHERE id = ?",
                (status, strategy_id),
            )
            return cursor.rowcount > 0

    def get_strategy(self, strategy_id: int) -> Optional[GoalStrategy]:
        """Get a single strategy by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM goal_strategies WHERE id = ?",
                (strategy_id,),
            ).fetchone()
            return GoalStrategy.from_row(dict(row)) if row else None

    # --- Daily Reviews ---

    def add_daily_review(self, review: DailyReview) -> int:
        """Insert a new daily review. Returns the review ID."""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO daily_reviews (review_date, prompt_text, user_response, summary)
                   VALUES (?, ?, ?, ?)""",
                (review.review_date, review.prompt_text, review.user_response, review.summary),
            )
            return cursor.lastrowid

    def get_daily_review(self, date: str) -> Optional[DailyReview]:
        """Get the daily review for a specific date (YYYY-MM-DD)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_reviews WHERE review_date = ?", (date,)
            ).fetchone()
            return DailyReview.from_row(dict(row)) if row else None

    def get_recent_reviews(self, limit: int = 7) -> list[DailyReview]:
        """Get recent daily reviews, most recent first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_reviews ORDER BY review_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [DailyReview.from_row(dict(r)) for r in rows]

    def update_daily_review(
        self, review_id: int, user_response: str, summary: str
    ) -> bool:
        """Update a daily review with user response and summary."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE daily_reviews SET user_response = ?, summary = ? WHERE id = ?",
                (user_response, summary, review_id),
            )
            return cursor.rowcount > 0

    # --- Agent Executions ---

    def log_agent_execution(
        self,
        agent_id: str,
        query: str,
        params: dict,
        success: bool,
        output_summary: str,
        artifacts: dict,
        error: str,
        duration_ms: int,
    ) -> int:
        """Log an agent execution for history and debugging."""
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO agent_executions
                   (agent_id, query, params, success, output_summary,
                    artifacts, error, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    query,
                    json.dumps(params),
                    int(success),
                    output_summary[:500],
                    json.dumps(artifacts),
                    error,
                    duration_ms,
                ),
            )
            return cursor.lastrowid

    def get_agent_executions(
        self, agent_id: Optional[str] = None, limit: int = 20
    ) -> list[dict]:
        """Get recent agent executions, optionally filtered by agent_id."""
        with self._conn() as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM agent_executions WHERE agent_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (agent_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_executions ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    # --- Tasks for Goals ---

    def get_tasks_for_goal(self, goal_id: int) -> list[Task]:
        """Get all tasks linked to a specific goal."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE goal_id = ? "
                "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                "created_at DESC",
                (goal_id,),
            ).fetchall()
            return [self._task_from_row(dict(r)) for r in rows]
