"""Tests for the Apple Recents utility (mdfind-based file discovery)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from giva.utils.recents import (
    RecentFile,
    _format_age,
    _format_size,
    format_recent_files,
    get_recent_files,
)


# --- _format_size ---


def test_format_size_bytes():
    assert _format_size(500) == "500 B"


def test_format_size_kb():
    assert _format_size(4096) == "4.0 KB"


def test_format_size_mb():
    result = _format_size(1_500_000)
    assert "MB" in result
    assert result == "1.4 MB"


def test_format_size_gb():
    result = _format_size(2_500_000_000)
    assert "GB" in result
    assert result == "2.3 GB"


# --- _format_age ---


def test_format_age_just_now():
    now = datetime.now()
    assert _format_age(now, now - timedelta(seconds=30)) == "just now"


def test_format_age_minutes():
    now = datetime.now()
    assert _format_age(now, now - timedelta(minutes=15)) == "15m ago"


def test_format_age_hours():
    now = datetime.now()
    assert _format_age(now, now - timedelta(hours=5)) == "5h ago"


def test_format_age_days():
    now = datetime.now()
    assert _format_age(now, now - timedelta(days=3)) == "3d ago"


def test_format_age_none():
    assert _format_age(datetime.now(), None) == "unknown"


# --- get_recent_files ---


@patch("giva.utils.recents.subprocess.run")
@patch("giva.utils.recents.os.stat")
@patch("giva.utils.recents.os.path.isfile")
def test_get_recent_files_basic(mock_isfile, mock_stat, mock_run):
    """Should return files from mdfind output."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="/Users/test/Documents/report.pdf\n/Users/test/Desktop/notes.txt\n",
    )
    mock_isfile.return_value = True
    stat_result = MagicMock()
    stat_result.st_mtime = datetime.now().timestamp()
    stat_result.st_size = 1024
    mock_stat.return_value = stat_result

    files = get_recent_files(hours=24, limit=10)
    assert len(files) == 2
    assert files[0].name in ("report.pdf", "notes.txt")


@patch("giva.utils.recents.subprocess.run")
@patch("giva.utils.recents.os.stat")
@patch("giva.utils.recents.os.path.isfile")
def test_get_recent_files_filters_hidden(mock_isfile, mock_stat, mock_run):
    """Should filter out paths with hidden directory segments."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            "/Users/test/Documents/report.pdf\n"
            "/Users/test/.git/config\n"
            "/Users/test/code/.cache/data.bin\n"
            "/Users/test/.Trash/old.txt\n"
        ),
    )
    mock_isfile.return_value = True
    stat_result = MagicMock()
    stat_result.st_mtime = datetime.now().timestamp()
    stat_result.st_size = 512
    mock_stat.return_value = stat_result

    files = get_recent_files(hours=24, limit=20)
    assert len(files) == 1
    assert files[0].name == "report.pdf"


@patch("giva.utils.recents.subprocess.run")
@patch("giva.utils.recents.os.stat")
@patch("giva.utils.recents.os.path.isfile")
def test_get_recent_files_filters_system_paths(mock_isfile, mock_stat, mock_run):
    """Should filter out system paths."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            "/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/os.py\n"
            "/System/Library/Fonts/Helvetica.ttc\n"
            "/private/var/log/system.log\n"
            "/Applications/Safari.app/Contents/Info.plist\n"
            "/Users/test/Documents/ok.pdf\n"
        ),
    )
    mock_isfile.return_value = True
    stat_result = MagicMock()
    stat_result.st_mtime = datetime.now().timestamp()
    stat_result.st_size = 256
    mock_stat.return_value = stat_result

    files = get_recent_files(hours=24, limit=20)
    assert len(files) == 1
    assert files[0].name == "ok.pdf"


@patch("giva.utils.recents.subprocess.run")
@patch("giva.utils.recents.os.stat")
@patch("giva.utils.recents.os.path.isfile")
def test_get_recent_files_filters_noise_substrings(mock_isfile, mock_stat, mock_run):
    """Should filter out node_modules, __pycache__, .app paths."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            "/Users/test/project/node_modules/react/index.js\n"
            "/Users/test/project/__pycache__/main.cpython-311.pyc\n"
            "/Users/test/Some.app/Contents/MacOS/binary\n"
            "/Users/test/project/main.py\n"
        ),
    )
    mock_isfile.return_value = True
    stat_result = MagicMock()
    stat_result.st_mtime = datetime.now().timestamp()
    stat_result.st_size = 100
    mock_stat.return_value = stat_result

    files = get_recent_files(hours=24, limit=20)
    assert len(files) == 1
    assert files[0].name == "main.py"


@patch("giva.utils.recents.subprocess.run")
def test_get_recent_files_subprocess_error(mock_run):
    """Should return empty list on non-zero returncode."""
    mock_run.return_value = MagicMock(returncode=1, stderr="Error")
    files = get_recent_files()
    assert files == []


@patch("giva.utils.recents.subprocess.run")
def test_get_recent_files_timeout(mock_run):
    """Should return empty list on timeout."""
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired("mdfind", 10)
    files = get_recent_files()
    assert files == []


@patch("giva.utils.recents.subprocess.run")
@patch("giva.utils.recents.os.stat")
@patch("giva.utils.recents.os.path.isfile")
def test_get_recent_files_sort_order(mock_isfile, mock_stat, mock_run):
    """Should sort by last_used descending."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="/Users/test/old.txt\n/Users/test/new.txt\n",
    )
    mock_isfile.return_value = True

    now = datetime.now()
    times = iter([
        (now - timedelta(hours=10)).timestamp(),  # old.txt
        now.timestamp(),                           # new.txt
    ])

    def stat_side_effect(p):
        result = MagicMock()
        result.st_mtime = next(times)
        result.st_size = 100
        return result

    mock_stat.side_effect = stat_side_effect

    files = get_recent_files(hours=24, limit=10)
    assert len(files) == 2
    assert files[0].name == "new.txt"  # most recent first
    assert files[1].name == "old.txt"


@patch("giva.utils.recents.subprocess.run")
@patch("giva.utils.recents.os.stat")
@patch("giva.utils.recents.os.path.isfile")
def test_get_recent_files_limit(mock_isfile, mock_stat, mock_run):
    """Should respect the limit parameter."""
    paths = "\n".join(f"/Users/test/file{i}.txt" for i in range(10))
    mock_run.return_value = MagicMock(returncode=0, stdout=paths)
    mock_isfile.return_value = True
    stat_result = MagicMock()
    stat_result.st_mtime = datetime.now().timestamp()
    stat_result.st_size = 100
    mock_stat.return_value = stat_result

    files = get_recent_files(hours=24, limit=3)
    assert len(files) == 3


# --- format_recent_files ---


def test_format_recent_files_basic():
    """Should format files with name, directory, size, and age."""
    now = datetime.now()
    files = [
        RecentFile(
            path="/Users/test/Documents/report.pdf",
            name="report.pdf",
            last_used=now - timedelta(hours=2),
            size_bytes=1_200_000,
        ),
    ]
    # Patch Path.home() to get consistent ~ replacement
    with patch("giva.utils.recents.Path.home", return_value=type("P", (), {"__str__": lambda s: "/Users/test"})()  # noqa: E501
    ):
        result = format_recent_files(files)
    assert "Recently used files:" in result
    assert "report.pdf" in result
    assert "1.1 MB" in result
    assert "2h ago" in result


def test_format_recent_files_empty():
    """Should return empty string for empty list."""
    assert format_recent_files([]) == ""


def test_format_recent_files_max_items():
    """Should respect max_items parameter."""
    now = datetime.now()
    files = [
        RecentFile(
            path=f"/Users/test/file{i}.txt",
            name=f"file{i}.txt",
            last_used=now - timedelta(hours=i),
            size_bytes=100,
        )
        for i in range(10)
    ]
    result = format_recent_files(files, max_items=3)
    # Header + 3 items = 4 lines
    lines = [ln for ln in result.split("\n") if ln.strip()]
    assert len(lines) == 4  # header + 3 items
