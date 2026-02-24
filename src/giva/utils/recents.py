"""Apple Recents: find recently used files via Spotlight mdfind.

Uses the ``kMDItemLastUsedDate`` Spotlight attribute to discover files the
user has recently opened.  Hidden directories (``.git``, ``.cache``, etc.)
and system paths are filtered out.  This is a local subprocess call —
no MCP server or LLM is involved.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Path segments starting with a dot → hidden directories/files
_HIDDEN_SEGMENT_RE = re.compile(r"/\.[^/]+")

# System / noise paths to always exclude
_EXCLUDE_PREFIXES = (
    "/Library/",
    "/System/",
    "/private/",
    "/Applications/",
)

_EXCLUDE_SUBSTRINGS = (
    "node_modules/",
    "__pycache__/",
    ".app/",
)


@dataclass
class RecentFile:
    """A recently used file discovered via Spotlight."""

    path: str
    name: str
    last_used: Optional[datetime]
    size_bytes: int


def get_recent_files(
    hours: int = 48,
    limit: int = 20,
    exclude_hidden: bool = True,
) -> list[RecentFile]:
    """Find recently used files via Spotlight ``mdfind``.

    Args:
        hours: Look back this many hours from now.
        limit: Max files to return.
        exclude_hidden: Filter out paths containing ``/.`` segments
            (e.g. ``.git``, ``.cache``, ``.Trash``).

    Returns a list of :class:`RecentFile` sorted by *last_used* descending.
    Returns ``[]`` on any error (subprocess failure, timeout, etc.).
    """
    seconds = hours * 3600
    query = f"kMDItemLastUsedDate >= $time.now(-{seconds})"

    try:
        result = subprocess.run(
            ["mdfind", query],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.debug("mdfind returned %d: %s", result.returncode, result.stderr.strip())
            return []
        raw_paths = result.stdout.strip().split("\n") if result.stdout.strip() else []
    except subprocess.TimeoutExpired:
        log.warning("mdfind timed out after 10s")
        return []
    except Exception as exc:
        log.debug("mdfind error: %s", exc)
        return []

    files: list[RecentFile] = []
    for p in raw_paths:
        if not p or not os.path.isfile(p):
            continue

        # Filter hidden directories
        if exclude_hidden and _HIDDEN_SEGMENT_RE.search(p):
            continue

        # Filter system / noise paths
        if any(p.startswith(prefix) for prefix in _EXCLUDE_PREFIXES):
            continue
        if any(sub in p for sub in _EXCLUDE_SUBSTRINGS):
            continue

        try:
            stat = os.stat(p)
            mtime = datetime.fromtimestamp(stat.st_mtime)
            files.append(RecentFile(
                path=p,
                name=os.path.basename(p),
                last_used=mtime,
                size_bytes=stat.st_size,
            ))
        except OSError:
            continue  # file may have been deleted between mdfind and stat

    # Sort by last_used descending, then truncate
    files.sort(key=lambda f: f.last_used or datetime.min, reverse=True)
    return files[:limit]


def format_recent_files(files: list[RecentFile], max_items: int = 10) -> str:
    """Format recent files for inclusion in LLM context or onboarding.

    Returns a human-readable text block, e.g.::

        Recently used files:
          - report.pdf (~/Documents, 1.2 MB, 2h ago)
          - main.py (~/Developer/Giva/src, 4.1 KB, 5h ago)
    """
    if not files:
        return ""

    home = str(Path.home())
    now = datetime.now()
    lines = ["Recently used files:"]

    for f in files[:max_items]:
        # Shorten path: replace home with ~
        display_dir = os.path.dirname(f.path)
        if display_dir.startswith(home):
            display_dir = "~" + display_dir[len(home):]

        # Human-readable size
        size = _format_size(f.size_bytes)

        # Human-readable age
        age = _format_age(now, f.last_used) if f.last_used else "unknown"

        lines.append(f"  - {f.name} ({display_dir}, {size}, {age})")

    return "\n".join(lines)


def _format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _format_age(now: datetime, then: Optional[datetime]) -> str:
    """Format time delta as human-readable age string."""
    if not then:
        return "unknown"
    delta = now - then
    if delta < timedelta(minutes=1):
        return "just now"
    elif delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}m ago"
    elif delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    else:
        return f"{delta.days}d ago"
