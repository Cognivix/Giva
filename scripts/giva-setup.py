#!/usr/bin/env python3
"""One-shot bootstrap: create venv, install giva, write launchd plist.

This script runs with SYSTEM python (not the venv).  Its only job is to
create the virtual environment, install the giva package, and write the
launchd plist so the daemon can start.  Once done, the giva-server daemon
takes over all remaining setup (model downloads, config, etc.).

Usage:
    python3 giva-setup.py --project-root /path/to/Giva

Output: JSON lines to stdout for the SwiftUI app to parse.
Exit code: 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "giva"
VENV_DIR = DATA_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python3"
VENV_PIP = VENV_DIR / "bin" / "pip"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.giva.server.plist"
LAUNCHD_LABEL = "com.giva.server"
CHECKPOINT_PATH = DATA_DIR / "bootstrap.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def emit(step: str, status: str, **kwargs):
    """Emit a JSON progress line to stdout for the app to parse."""
    msg = {"step": step, "status": status, **kwargs}
    print(json.dumps(msg), flush=True)


def _get_python_version(path: str) -> tuple[int, int] | None:
    """Return (major, minor) for a python executable, or None."""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        # "Python 3.13.2"
        parts = result.stdout.strip().replace("Python ", "").split(".")
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def _venv_healthy() -> bool:
    """Check if the venv python runs successfully."""
    if not VENV_PYTHON.exists():
        return False
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "--version"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _giva_importable() -> bool:
    """Check if giva is importable in the venv."""
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import giva; print(giva.__version__)"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def find_python() -> str:
    """Find Python 3.11+ on the system."""
    emit("finding_python", "running")
    candidates = [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            version = _get_python_version(path)
            if version and (version[0] > 3 or (version[0] == 3 and version[1] >= 11)):
                emit("finding_python", "done", detail=path,
                     version=f"{version[0]}.{version[1]}")
                return path

    # Fallback: which python3
    try:
        result = subprocess.run(
            ["/usr/bin/which", "python3"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            path = result.stdout.strip()
            if path:
                version = _get_python_version(path)
                if version and (version[0] > 3 or (version[0] == 3 and version[1] >= 11)):
                    emit("finding_python", "done", detail=path,
                         version=f"{version[0]}.{version[1]}")
                    return path
    except Exception:
        pass

    emit("finding_python", "failed",
         error="Python 3.11+ not found. Install via: brew install python3")
    sys.exit(1)


def create_venv(python: str) -> None:
    """Create the venv if it doesn't exist or is broken."""
    emit("creating_venv", "running")

    if VENV_PYTHON.exists() and _venv_healthy():
        emit("creating_venv", "done", detail="already exists")
        return

    # Remove broken venv
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR, ignore_errors=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [python, "-m", "venv", str(VENV_DIR)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        emit("creating_venv", "failed",
             error=f"venv creation failed: {result.stderr[-500:]}")
        sys.exit(1)

    emit("creating_venv", "done")


def install_deps(project_root: str) -> None:
    """Install giva into the venv."""
    emit("installing_deps", "running", detail="upgrading pip")

    # Upgrade pip
    result = subprocess.run(
        [str(VENV_PIP), "install", "--upgrade", "pip"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        emit("installing_deps", "failed",
             error=f"pip upgrade failed: {result.stderr[-500:]}")
        sys.exit(1)

    emit("installing_deps", "running", detail="pip install -e .[voice]")

    # Install project
    result = subprocess.run(
        [str(VENV_PIP), "install", "-e", ".[voice]"],
        cwd=project_root,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        emit("installing_deps", "failed",
             error=f"pip install failed: {result.stderr[-500:]}")
        sys.exit(1)

    # Verify import
    verify = subprocess.run(
        [str(VENV_PYTHON), "-c", "import giva; print(giva.__version__)"],
        capture_output=True, text=True, timeout=30,
    )
    if verify.returncode != 0:
        emit("installing_deps", "failed",
             error="giva not importable after install")
        sys.exit(1)

    version = verify.stdout.strip()
    emit("installing_deps", "done", detail=f"giva v{version}")


def write_plist() -> None:
    """Write the launchd plist for the giva-server daemon."""
    emit("writing_plist", "running")

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            str(VENV_PYTHON),
            "-m", "giva.server",
        ],
        "RunAtLoad": True,
        "KeepAlive": {
            "SuccessfulExit": False,
        },
        "StandardOutPath": str(log_dir / "server.log"),
        "StandardErrorPath": str(log_dir / "server.err"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin:/sbin",
            "HOME": str(Path.home()),
        },
        "ProcessType": "Interactive",
    }

    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    emit("writing_plist", "done", detail=str(PLIST_PATH))


def write_checkpoint() -> None:
    """Write initial bootstrap checkpoint so the server knows where to resume."""
    checkpoint = {
        "version": 1,
        "checkpoint": "venv_ok",
        "steps_completed": ["venv_ok"],
        "current_step": None,
        "progress": {},
        "error": None,
        "updated_at": None,
    }
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(checkpoint, indent=2))
    emit("checkpoint", "done")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Giva first-run setup")
    parser.add_argument(
        "--project-root", required=True,
        help="Path to the Giva project root (contains pyproject.toml)",
    )
    args = parser.parse_args()

    project_root = args.project_root
    if not os.path.isfile(os.path.join(project_root, "pyproject.toml")):
        emit("setup", "failed",
             error=f"pyproject.toml not found in {project_root}")
        sys.exit(1)

    # Fast path: if everything is already healthy, just ensure plist + checkpoint
    if _venv_healthy() and _giva_importable():
        emit("finding_python", "done", detail="skipped (venv healthy)")
        emit("creating_venv", "done", detail="already exists")
        emit("installing_deps", "done", detail="already installed")
        write_plist()
        write_checkpoint()
        emit("complete", "done")
        return

    # Full setup
    python = find_python()
    create_venv(python)
    install_deps(project_root)
    write_plist()
    write_checkpoint()

    emit("complete", "done")


if __name__ == "__main__":
    main()
