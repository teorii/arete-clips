"""Locate the ffmpeg toolchain."""

import os
import shutil
from pathlib import Path

_WINGET = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft/WinGet/Packages"
)


def _find(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if _WINGET.exists():
        for candidate in _WINGET.rglob(f"{name}.exe"):
            return str(candidate)
    raise FileNotFoundError(
        f"{name} not found. Install with: winget install --id Gyan.FFmpeg -e"
    )


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")
