"""AppleScript and JXA runner helpers for macOS app interaction."""

from __future__ import annotations

import json
import logging
import subprocess

log = logging.getLogger(__name__)


def run_applescript(script: str, timeout: int = 120) -> str:
    """Run an AppleScript and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        log.error("AppleScript error: %s", result.stderr.strip())
        raise RuntimeError(f"AppleScript failed: {result.stderr.strip()}")
    return result.stdout.strip()


def run_jxa(script: str, timeout: int = 120) -> str:
    """Run a JavaScript for Automation script and return stdout."""
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        log.error("JXA error: %s", result.stderr.strip())
        raise RuntimeError(f"JXA failed: {result.stderr.strip()}")
    return result.stdout.strip()


def run_jxa_json(script: str, timeout: int = 120) -> list | dict:
    """Run a JXA script that returns JSON and parse the result."""
    raw = run_jxa(script, timeout=timeout)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("Failed to parse JXA JSON output: %s (raw: %s)", e, raw[:200])
        return []


def check_fda_access() -> bool:
    """Check if we have Full Disk Access to Apple Mail data."""
    script = """
var fm = $.NSFileManager.defaultManager;
var path = $.NSString.alloc.initWithUTF8String(
    ObjC.unwrap(fm.homeDirectoryForCurrentUser.path) +
    "/Library/Group Containers/group.com.apple.mail"
);
var readable = fm.isReadableFileAtPath(path);
readable ? "true" : "false";
"""
    try:
        result = run_jxa(script, timeout=5)
        return result == "true"
    except Exception:
        return False
