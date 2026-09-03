"""Capture daemon: hold a rolling buffer, and on hotkey turn it into a link.

Run it with the server up:

    python -m capture.daemon              # F9 clips the last 30 seconds
    python -m capture.daemon --test       # warm up, clip once, exit
    python -m capture.daemon --probe      # list capture outputs and quit
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from paths import data_dir
from problems import warn

from . import sound
from .cutter import ClipError, flush
from .config import get_capture_settings
from .loopback import Loopback
from .ffmpeg import FFMPEG
from .ringbuffer import RingBuffer
from .uploader import UploadError, Uploader

CLIP_OUT_DIR = data_dir() / "clips_out"
JOURNAL = CLIP_OUT_DIR / ".pending.json"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def gpu_name() -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError) as exc:
        warn("gpu name", exc, "capture metadata will say unknown")
    return "unknown"


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-16le"), shell=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        warn("clipboard", exc, "the link was not copied, read it above")
        return False


def probe_outputs() -> None:
    """Show which desktop output index maps to which display."""
    print("Probing Desktop Duplication outputs (2s each)...\n")
    for idx in range(4):
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"ddagrab=output_idx={idx}:framerate=30",
             "-t", "0.5", "-f", "null", "-"],
            capture_output=True, text=True,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )
        if result.returncode == 0:
            print(f"  output_idx={idx}  available")
        else:
            first = (result.stderr.strip().splitlines() or ["unavailable"])[0]
            print(f"  output_idx={idx}  {first[:90]}")
    print("\nSet DDAGRAB_OUTPUT_IDX in .env to the display you play on.")


class Daemon:
    def __init__(self) -> None:
        self.s = get_capture_settings()
        self.ring = RingBuffer(self.s)
        # Its own recording of the default playback device. ffmpeg's dshow
        # input cannot see one, so this is the only way to capture what the
        # machine is actually playing rather than a named capture device.
        self.audio = Loopback()
        self.uploader = Uploader(
            self.s.api_base_url, JOURNAL, api_key=self.s.arete_api_key
        )
        self.gpu = gpu_name()
        self._busy = threading.Lock()

    def clip_name(self, when: datetime) -> str:
        """What to call a clip nobody has renamed yet.

        The screen and the time, because both are true. Naming a clip after the
        program in front on the captured display sounded better and read worse:
        the answer was whatever happened to hold focus at the moment the hotkey
        arrived, which on a second monitor is rarely the game and sometimes the
        overlay that was clicked to start recording.

        The display is numbered as Windows numbers it, so it matches the source
        shown in the app rather than the index ddagrab counts from.
        """
        shown = self.s.ddagrab_output_idx + 1
        try:
            from .windows import monitors

            found = monitors()
            index = self.s.ddagrab_output_idx
            if index < len(found) and found[index].get("number"):
                shown = found[index]["number"]
        except Exception as exc:  # noqa: BLE001
            # Never worth failing a clip over: a clip named by index is fine, a
            # lost one is not.
            warn("clip name", exc, "this clip is named by capture order")

        return f"Display {shown} at {when.astimezone().strftime('%H.%M')}"

    def capture_meta(self, width: int, height: int) -> dict:
        return {
            "encoder": "h264_nvenc",
            "gpu": self.gpu,
            "capture_api": "ddagrab (Desktop Duplication)",
            "audio": self.audio.device or self.ring.audio_device or "none",
            "width": width,
            "height": height,
            "fps": self.s.capture_fps,
            "bitrate": self.s.capture_bitrate,
            "segment_seconds": self.s.segment_seconds,
            "buffer_seconds": self.s.buffer_seconds,
            "output_idx": self.s.ddagrab_output_idx,
        }

    def make_clip(self) -> dict:
        """Flush the buffer into a clip.

        Returns what happened: uploaded with a link, held locally awaiting one,
        or failed.
        """
        if not self._busy.acquire(blocking=False):
            print("  ... already making a clip, ignoring")
            return {"status": "busy", "path": None, "url": None}
        try:
            captured_at = datetime.now(timezone.utc)
            started = time.perf_counter()
            name = captured_at.strftime("clip_%Y%m%d_%H%M%S")

            result = flush(self.ring, CLIP_OUT_DIR, name)
            remuxed = time.perf_counter()
            print(
                f"  cut {result.duration_ms / 1000:.1f}s "
                f"{result.width}x{result.height} "
                f"{result.size_bytes / 1_048_576:.1f} MB "
                f"in {remuxed - started:.2f}s"
            )

            sound.clip_saved()
            meta = self.capture_meta(result.width, result.height)
            title = self.clip_name(captured_at)

            if not self.s.auto_upload:
                path = self.uploader.hold(result, captured_at, meta, title=title)
                print(f"  kept locally in {time.perf_counter() - started:.2f}s")
                print("  waiting for you to generate a link")
                return {"status": "held", "path": path, "url": None}

            url = self.uploader.submit(result, captured_at, meta, title=title)
            print(f"  uploaded in {time.perf_counter() - remuxed:.2f}s")
            print(f"  -> {url}   (time to link: {time.perf_counter() - started:.2f}s)")
            if copy_to_clipboard(url):
                print("  copied to clipboard")
            return {"status": "uploaded", "path": None, "url": url}
        except ClipError as exc:
            sound.clip_failed()
            print(f"  clip failed: {exc}")
            return {"status": "failed", "path": None, "url": None, "error": str(exc)}
        except (UploadError, OSError) as exc:
            # The clip itself is safe on disk, so this is a softer failure than
            # not capturing at all. Still worth hearing.
            sound.clip_failed()
            print(f"  upload failed, kept in the journal for retry: {exc}")
            return {"status": "failed", "path": None, "url": None, "error": str(exc)}
        finally:
            self._busy.release()
        return {"status": "failed", "path": None, "url": None}

    def start_buffer(self) -> None:
        # Opened before the encoder, which has to be told the sample rate and
        # channel count before it starts, and only the device knows them. A
        # device that will not open costs the sound, never the picture.
        self.audio.open()
        self.ring.loopback = self.audio

        print(f"Starting ring buffer: {self.s.buffer_seconds}s window, "
              f"{self.s.segment_seconds}s segments, output_idx={self.s.ddagrab_output_idx}")
        self.ring.start()
        time.sleep(self.s.segment_seconds + 1.5)
        if not self.ring.is_running():
            raise SystemExit(
                f"capture process exited immediately.\n{self.ring.tail_log()}"
            )
        print(f"Capturing on {self.gpu} via h264_nvenc")
        if self.audio.device:
            print(f"Desktop audio: {self.audio.device}")
        else:
            print("Desktop audio: unavailable, clips will be silent.")

    def drain_journal(self) -> None:
        swept = self.uploader.sweep_staging(CLIP_OUT_DIR)
        if swept:
            print(f"Cleared {swept} already-uploaded staging file(s)")
        outstanding = self.uploader.pending_count()
        if outstanding:
            print(f"Retrying {outstanding} clip(s) left over from a previous run...")
            for url in self.uploader.retry_pending():
                print(f"  recovered -> {url}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="capture.daemon")
    parser.add_argument("--test", action="store_true",
                        help="warm the buffer, take one clip, exit")
    parser.add_argument("--probe", action="store_true",
                        help="list available capture outputs and exit")
    args = parser.parse_args()

    if args.probe:
        probe_outputs()
        return

    daemon = Daemon()
    daemon.drain_journal()
    daemon.start_buffer()

    try:
        if args.test:
            wait = daemon.s.clip_seconds + daemon.s.segment_seconds
            print(f"Filling the buffer for {wait}s, then clipping once...")
            time.sleep(wait)
            daemon.make_clip()
            return

        from .hotkey import pump

        key = daemon.s.hotkey_vk
        print(f"\nReady. Press F9 (vk={key:#04x}) to clip the last "
              f"{daemon.s.clip_seconds}s. Ctrl+C to stop.\n")

        def on_press() -> None:
            print("F9 pressed")
            threading.Thread(target=daemon.make_clip, daemon=True).start()

        pump(key, on_press)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        daemon.ring.stop()
        daemon.audio.close()


if __name__ == "__main__":
    sys.exit(main())
