"""Schema versioning and migration support."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from giva.db.store import SCHEMA_VERSION


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
