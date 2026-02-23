"""Schema versioning and migration support."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from giva.db.store import SCHEMA_VERSION

log = logging.getLogger(__name__)

# Migrations keyed by target version. Each value is a SQL script that upgrades
# from the previous version. Use executescript() so multiple statements work.
MIGRATIONS: dict[int, str] = {
    2: """
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
    """,
    3: "",  # ALTER-only migration; statements in _ALTER_MIGRATIONS[3]
    4: "",  # ALTER-only migration; statements in _ALTER_MIGRATIONS[4]
}

# ALTER TABLE must run as a separate execute() (not inside executescript).
_ALTER_MIGRATIONS: dict[int, list[str]] = {
    2: [
        "ALTER TABLE tasks ADD COLUMN goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL",
    ],
    3: [
        "ALTER TABLE goal_strategies ADD COLUMN suggested_objectives TEXT DEFAULT '[]'",
    ],
    4: [
        "ALTER TABLE conversations ADD COLUMN goal_id INTEGER"
        " REFERENCES goals(id) ON DELETE CASCADE",
    ],
}


def check_schema(db_path: Path) -> bool:
    """Check if the database schema is at the expected version."""
    if not db_path.exists():
        return True  # Will be created fresh
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        return row[0] == SCHEMA_VERSION
    except sqlite3.OperationalError:
        return False  # Table doesn't exist yet
    finally:
        conn.close()


def migrate(db_path: Path) -> bool:
    """Run any pending migrations. Returns True if migrations were applied.

    Safe to call on a fresh database (no-ops if already at current version).
    """
    if not db_path.exists():
        return False  # Will be created fresh by Store._init_db()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        # Get current schema version
        try:
            row = conn.execute(
                "SELECT MAX(version) as v FROM schema_version"
            ).fetchone()
            current = row[0] if row and row[0] else 0
        except sqlite3.OperationalError:
            return False  # No schema_version table — not a Giva DB

        if current >= SCHEMA_VERSION:
            return False  # Already up to date

        applied = False
        for version in sorted(MIGRATIONS.keys()):
            if version > current:
                log.info("Applying migration to schema version %d", version)
                conn.executescript(MIGRATIONS[version])

                # Run ALTER TABLE statements separately
                for stmt in _ALTER_MIGRATIONS.get(version, []):
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as e:
                        # Column may already exist (idempotent)
                        if "duplicate column" not in str(e).lower():
                            raise
                        log.debug("Column already exists, skipping: %s", e)

                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
                conn.commit()
                applied = True
                log.info("Migration to version %d complete", version)

        return applied
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
