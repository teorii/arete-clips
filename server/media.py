"""Server-side clip editing.

The rule everywhere else in this service is that video bytes never pass through
the application tier: the client PUTs to storage and players read from it. That
still holds for upload and playback. Trimming breaks it on purpose, because on
a single-user desktop app the server is the same machine as the client, and
shipping 44 MB out and back to cut two seconds off would be pure overhead.

The cut itself is a remux. `-ss` before the input seeks to the nearest earlier
keyframe and `-c copy` copies the compressed bytes, so a trim is near-instant
and lossless. The cost is granularity: cuts land on keyframes, which the
capture side places at segment boundaries. Frame accuracy would mean
re-encoding the partial group of pictures at each edge, which is the trade the
design notes describe and which is not worth making here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from capture.ffmpeg import FFMPEG, FFPROBE

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaError(RuntimeError):
    pass


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, creationflags=_NO_WINDOW)


def duration_seconds(path: Path) -> float:
    result = _run([
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise MediaError(f"could not read duration: {result.stderr.strip()}")
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise MediaError("duration missing from ffprobe output") from exc


def trim(source: Path, destination: Path, start_s: float, end_s: float) -> None:
    """Cut [start_s, end_s) out of `source` without re-encoding."""
    if end_s <= start_s:
        raise MediaError("end must be after start")

    result = _run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        # -ss before -i is the fast seek, and the one that snaps to a keyframe.
        "-ss", f"{start_s:.3f}",
        "-to", f"{end_s:.3f}",
        "-i", str(source),
        "-c", "copy",
        "-movflags", "+faststart",
        str(destination),
    ])
    if result.returncode != 0 or not destination.exists():
        raise MediaError(f"trim failed: {result.stderr.strip()}")
    if destination.stat().st_size == 0:
        raise MediaError("trim produced an empty file")


def poster(source: Path, destination: Path, at_s: float) -> bool:
    """Grab a poster frame. Best effort: a clip without one still plays."""
    result = _run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{max(at_s, 0):.3f}", "-i", str(source),
        "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4",
        str(destination),
    ])
    return result.returncode == 0 and destination.exists()
