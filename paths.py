"""Where the app keeps its files.

Frozen into a one-file executable, the code runs from a temp directory that is
deleted on exit, so nothing may be written beside it and nothing there survives
a restart. Config and captured clips go to the usual per-user location instead.

Running from source, everything stays in the project directory, so development
does not scatter files into AppData.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Arete"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def bundle_dir() -> Path:
    """Where read-only bundled files live: ffmpeg, the built web app, icons."""
    packed = getattr(sys, "_MEIPASS", None)
    return Path(packed) if packed else Path(__file__).resolve().parent


def data_dir() -> Path:
    """Writable per-user directory. Created on demand."""
    if is_frozen():
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        directory = base / APP_DIR_NAME
    else:
        directory = Path(__file__).resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def config_file() -> Path:
    """Settings file. `.env` in development so nothing changes there."""
    return data_dir() / ("config.env" if is_frozen() else ".env")


def is_configured() -> bool:
    """Whether setup has been completed on this machine."""
    path = config_file()
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return "ARETE_API_KEY=" in text and not any(
        line.strip() == "ARETE_API_KEY=" for line in text.splitlines()
    )
