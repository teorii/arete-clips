"""Locate the ffmpeg toolchain."""

import os
import shutil
from pathlib import Path

from paths import bundle_dir

_WINGET = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft/WinGet/Packages"
)


def _find(name: str) -> str:
    # Bundled copy first. A packaged app cannot assume ffmpeg is installed, and
    # picking up a stranger's PATH version would mean capture depending on
    # whatever build they happen to have.
    bundled = bundle_dir() / f"{name}.exe"
    if bundled.exists():
        return str(bundled)

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
