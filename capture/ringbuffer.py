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
import time
from pathlib import Path

from problems import warn

from .audio import find_loopback_device
from .config import CaptureSettings
from .ffmpeg import FFMPEG



# MPEG-TS is a stream of fixed 188-byte packets. The newest segment is copied
# while ffmpeg is still writing it, so it almost always ends part-way through
# one, and a decoder meeting half a packet reports a damaged bitstream:
#
#     [h264] error while decoding MB 46 71, bytestream -11
#
# The whole packets before the tear are perfectly good. Dropping the fragment
# costs at most 188 bytes of the final frame and removes the corruption.
_TS_PACKET = 188


def _trim_to_whole_packets(path: Path) -> int:
    """Cut a staged segment back to its last complete packet. Returns its size."""
    try:
        size = path.stat().st_size
        whole = size - (size % _TS_PACKET)
        if whole != size:
            with path.open("r+b") as handle:
                handle.truncate(whole)
        return whole
    except OSError as exc:
        warn("ring buffer", exc, f"{path.name} left out of this clip")
        return 0

class RingBuffer:
    def __init__(self, settings: CaptureSettings) -> None:
        self.s = settings
        self.dir = Path(settings.ring_buffer_dir).resolve()
        self.log_path = self.dir / "ffmpeg.log"
        self.proc: subprocess.Popen | None = None
        self._log = None
        self.audio_device: str | None = None
        self._audio_resolved = False

    # ------------------------------------------------------------- lifecycle

    def resolve_audio(self) -> str | None:
        """Look for a desktop-audio device once and remember the answer.

        Enumerating dshow devices spawns a process, so this must not happen on
        every restart the watchdog performs.
        """
        if not self._audio_resolved:
            self.audio_device = find_loopback_device(self.s.audio_device)
            self._audio_resolved = True
        return self.audio_device

    def command(self) -> list[str]:
        s = self.s
        gop = s.segment_seconds * s.capture_fps
        audio = self.resolve_audio()

        args = [
            FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-f", "lavfi",
            "-i", f"ddagrab=output_idx={s.ddagrab_output_idx}:framerate={s.capture_fps}",
        ]
        if audio:
            # A generous queue: the audio device and the screen do not produce
            # at the same cadence, and a full queue shows up as dropped sound.
            args += [
                "-thread_queue_size", "1024",
                "-f", "dshow",
                "-i", f"audio={audio}",
                "-map", "0:v", "-map", "1:a",
            ]
        args += [
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
        ]
        if audio:
            args += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
        args += [
            "-f", "segment",
            "-segment_time", str(s.segment_seconds),
            "-segment_wrap", str(s.segment_count),
            "-segment_format", "mpegts",
            "-reset_timestamps", "1",
            str(self.dir / "seg%03d.ts"),
        ]
        return args

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

    def seconds_since_last_write(self) -> float | None:
        """Age of the newest segment, or None when the ring is empty."""
        try:
            newest = max(
                (p.stat().st_mtime for p in self.dir.glob("*.ts")), default=None
            )
        except OSError:
            return None
        return None if newest is None else max(0.0, time.time() - newest)

    def is_stalled(self, tolerance_seconds: float) -> bool:
        """True when the process is alive but has stopped producing.

        A dead process is easy to spot. A wedged one is not: a GPU driver reset
        can leave ffmpeg running and writing nothing, which every liveness check
        reads as healthy right up until you press the hotkey and get an empty
        buffer. Segments arrive continuously, so silence is the real signal.
        """
        age = self.seconds_since_last_write()
        return age is not None and age > tolerance_seconds

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
            except OSError as exc:
                # The ring recycled this file mid-copy. The rest still make a
                # clip, just a shorter one, which is worth knowing about.
                warn("ring buffer", exc, f"{src.name} missing from this clip")
                continue
            if _trim_to_whole_packets(dst) > 0:
                staged.append(dst)
        return staged
