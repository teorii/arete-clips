"""Turn the tail of the ring buffer into a shareable MP4.

This is a remux, not a transcode. The segments are already H.264 and every
segment starts on an IDR frame, so the cut lands on a keyframe and the
compressed bytes are copied straight across. No decode, no re-encode, and the
whole thing finishes in well under a second regardless of clip length.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from problems import warn

from .config import get_capture_settings
from .ffmpeg import FFMPEG, FFPROBE
from .ringbuffer import RingBuffer

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class ClipResult:
    mp4_path: Path
    thumb_path: Path | None
    duration_ms: int
    width: int
    height: int
    size_bytes: int
    content_hash: str


class ClipError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    # Explicit stdin: a windowed build has no console, and a child left to
    # inherit an invalid handle hangs instead of failing.
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )


def probe(path: Path) -> dict:
    result = _run([
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise ClipError(f"ffprobe failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def flush(ring: RingBuffer, out_dir: Path, name: str) -> ClipResult:
    # Absolute: the remux runs with cwd set to the staging dir so the concat
    # list can use bare filenames, which would otherwise resolve the output
    # path against the wrong directory.
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / f"{name}.mp4"
    thumb_path = out_dir / f"{name}.jpg"

    with tempfile.TemporaryDirectory(prefix="clipflush_") as tmp:
        staging = Path(tmp)
        parts = ring.snapshot(staging)
        if not parts:
            raise ClipError(
                "ring buffer is empty. Is the capture process running? "
                f"ffmpeg log tail:\n{ring.tail_log()}"
            )

        listing = staging / "concat.txt"
        listing.write_text(
            "".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8"
        )

        remux = _run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-fflags", "+genpts",
            "-f", "concat", "-safe", "0", "-i", listing.name,
            "-c", "copy",
            # faststart moves the moov atom to the front so the player can
            # begin before the whole file has arrived.
            "-movflags", "+faststart",
            str(mp4_path),
        ], cwd=staging)
        if remux.returncode != 0 or not mp4_path.exists():
            raise ClipError(f"remux failed: {remux.stderr.strip()}")

    info = probe(mp4_path)
    stream = (info.get("streams") or [{}])[0]
    duration_s = float(info.get("format", {}).get("duration", 0.0))

    # A frame from the middle is a better poster than the first frame, which is
    # often a fade or a loading screen.
    thumb = _run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", f"{max(duration_s / 2, 0):.2f}", "-i", str(mp4_path),
        "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4",
        str(thumb_path),
    ])

    digest = hashlib.sha256()
    with mp4_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return ClipResult(
        mp4_path=mp4_path,
        thumb_path=thumb_path if thumb.returncode == 0 and thumb_path.exists() else None,
        duration_ms=int(duration_s * 1000),
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        size_bytes=mp4_path.stat().st_size,
        content_hash=digest.hexdigest(),
    )
