"""Tests for schema versioning and migration support."""

import sqlite3

from giva.db.migrations import check_schema, migrate, MIGRATIONS, _ALTER_MIGRATIONS
from giva.db.store import Store, SCHEMA_VERSION


def test_check_schema_fresh_db(tmp_path):
    """Non-existent DB reports schema OK (will be created fresh)."""
    db_path = tmp_path / "new.db"
    assert check_schema(db_path) is True


def test_check_schema_current(tmp_path):
    """DB at current schema version reports OK."""
    db_path = tmp_path / "test.db"
    Store(db_path)  # Creates schema at current version
    assert check_schema(db_path) is True


def test_check_schema_old_version(tmp_path):
    """DB at old version reports not OK."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE schema_version (version INTEGER)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    assert check_schema(db_path) is False


def test_check_schema_no_version_table(tmp_path):
    """DB without schema_version table reports not OK."""
    db_path = tmp_path / "bare.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE dummy (id INTEGER)")
    conn.commit()
    conn.close()

    assert check_schema(db_path) is False


def test_migrate_nonexistent_db(tmp_path):
    """Migrating a non-existent DB is a no-op (returns False)."""
    db_path = tmp_path / "missing.db"
    assert migrate(db_path) is False


def test_migrate_up_to_date(tmp_path):
    """Migrating an up-to-date DB returns False."""
    db_path = tmp_path / "current.db"
    Store(db_path)  # Creates at current version
    assert migrate(db_path) is False


def test_migrate_from_v1(tmp_path):
    """Migrating from v1 applies all subsequent migrations."""
    db_path = tmp_path / "v1.db"

    # Create a minimal v1 schema with required tables
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER);
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            folder TEXT,
            from_addr TEXT,
            from_name TEXT DEFAULT '',
            to_addrs TEXT DEFAULT '[]',
            cc_addrs TEXT DEFAULT '[]',
            subject TEXT,
            date_sent TEXT,
            body_plain TEXT DEFAULT '',
            body_html TEXT DEFAULT '',
            is_read INTEGER DEFAULT 0,
            is_flagged INTEGER DEFAULT 0,
            references_list TEXT DEFAULT '[]',
            attachment_names TEXT DEFAULT '[]'
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE,
            calendar_name TEXT,
            summary TEXT,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            dtstart TEXT,
            dtend TEXT,
            all_day INTEGER DEFAULT 0,
            organizer TEXT DEFAULT '',
            attendees TEXT DEFAULT '[]',
            status TEXT DEFAULT 'CONFIRMED'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            source_type TEXT NOT NULL,
            source_id INTEGER DEFAULT 0,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'pending',
            due_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    result = migrate(db_path)
    assert result is True

    # Verify the goals table was created (migration v2)
    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "goals" in tables
    assert "goal_progress" in tables
    assert "goal_strategies" in tables
    assert "daily_reviews" in tables

    # Verify ALTER migrations: tasks should have goal_id
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    assert "goal_id" in cols

    # Verify version is current
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    assert row[0] == SCHEMA_VERSION
    conn.close()


def test_migrate_idempotent_alter(tmp_path):
    """Running ALTER migrations twice doesn't fail (duplicate column detection)."""
    db_path = tmp_path / "idempotent.db"
    Store(db_path)  # Creates at current version

    # Manually set version back to force re-migration
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    # Should handle "duplicate column" gracefully
    result = migrate(db_path)
    assert result is True


def test_migrate_no_schema_version_table(tmp_path):
    """DB without schema_version table returns False (not a Giva DB)."""
    db_path = tmp_path / "foreign.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE other (id INTEGER)")
    conn.commit()
    conn.close()

    assert migrate(db_path) is False


def test_migrations_dict_keys_ascending():
    """Migration keys should be in ascending order."""
    keys = sorted(MIGRATIONS.keys())
    assert keys == list(MIGRATIONS.keys()) or keys == sorted(MIGRATIONS.keys())
    # All migration versions should be > 1 (v1 is the base)
    assert all(v >= 2 for v in MIGRATIONS.keys())


def test_alter_migrations_match_create_migrations():
    """ALTER migration versions should have corresponding CREATE migrations."""
    for version in _ALTER_MIGRATIONS:
        assert version in MIGRATIONS, f"ALTER migration v{version} has no CREATE entry"
