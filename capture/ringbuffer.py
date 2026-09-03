"""The rolling capture buffer.

ffmpeg captures the desktop through the Desktop Duplication API, which hands
back D3D11 hardware frames, and feeds them straight into NVENC. The frame never
leaves GPU memory: only the compressed stream, roughly 1.5 MB/s, crosses into
system RAM. Uncompressed 1440p60 would be about 900 MB/s over PCIe, so this is
the difference between a few percent of framerate and an unusable tool.

The buffer itself is the segment muxer. `-segment_wrap N` recycles filenames,
so the directory is a fixed-size ring that overwrites its own oldest segment.
It is disk-backed on purpose: games crash and drivers reset, and the thirty
seconds a player most wants to keep are very often the thirty seconds right
before something went wrong. A pure RAM buffer loses exactly that footage.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import CaptureSettings
from .ffmpeg import FFMPEG


class RingBuffer:
    def __init__(self, settings: CaptureSettings) -> None:
        self.s = settings
        self.dir = Path(settings.ring_buffer_dir).resolve()
        self.log_path = self.dir / "ffmpeg.log"
        self.proc: subprocess.Popen | None = None
        self._log = None

    # ------------------------------------------------------------- lifecycle

    def command(self) -> list[str]:
        s = self.s
        gop = s.segment_seconds * s.capture_fps
        return [
            FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-f", "lavfi",
            "-i", f"ddagrab=output_idx={s.ddagrab_output_idx}:framerate={s.capture_fps}",
            "-c:v", "h264_nvenc",
            # p4 is the balanced NVENC preset; low-latency tuning keeps the
            # encoder from buffering frames it would need to hold onto.
            "-preset", "p4", "-tune", "ll",
            "-rc", "cbr", "-b:v", s.capture_bitrate,
            "-maxrate", s.capture_bitrate, "-bufsize", s.capture_bitrate,
            # Every segment must open with an IDR frame, otherwise a segment
            # cannot be decoded on its own and concatenating them breaks.
            # This is also what makes the cut a byte copy instead of a re-encode.
            "-g", str(gop), "-forced-idr", "1",
            "-force_key_frames", f"expr:gte(t,n_forced*{s.segment_seconds})",
            "-f", "segment",
            "-segment_time", str(s.segment_seconds),
            "-segment_wrap", str(s.segment_count),
            "-segment_format", "mpegts",
            "-reset_timestamps", "1",
            str(self.dir / "seg%03d.ts"),
        ]

    def start(self) -> None:
        if self.is_running():
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        for stale in self.dir.glob("*.ts"):
            stale.unlink(missing_ok=True)
        self._log = self.log_path.open("w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            self.command(),
            stdout=self._log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log is not None:
            self._log.close()
            self._log = None
        self.proc = None

    def tail_log(self, lines: int = 12) -> str:
        if not self.log_path.exists():
            return "(no log)"
        return "\n".join(
            self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        )

    # ---------------------------------------------------------------- flush

    def snapshot(self, staging: Path) -> list[Path]:
        """Copy the newest segments out of the ring.

        Copy rather than read in place: the ring keeps advancing while we work,
        and a snapshot removes any chance of the writer recycling a filename
        underneath the remux.

        The newest segment is still being written when we grab it. We take it
        anyway, because a truncated MPEG-TS file is decodable up to its last
        complete packet, and the moment the player just reacted to lives in
        exactly that segment. Dropping it would cut off the payoff.
        """
        segments = sorted(self.dir.glob("*.ts"), key=lambda p: p.stat().st_mtime)
        if not segments:
            return []

        wanted = self.s.segments_per_clip + 1  # +1 for the in-progress segment
        selected = segments[-wanted:]

        staging.mkdir(parents=True, exist_ok=True)
        staged: list[Path] = []
        for index, src in enumerate(selected):
            dst = staging / f"part{index:03d}.ts"
            try:
                shutil.copy2(src, dst)
            except OSError:
                continue  # recycled mid-copy; the remaining parts still work
            if dst.stat().st_size > 0:
                staged.append(dst)
        return staged
