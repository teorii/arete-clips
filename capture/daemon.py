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

from .cutter import ClipError, flush
from .config import get_capture_settings
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
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-16le"), shell=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
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
        self.uploader = Uploader(
            self.s.api_base_url, JOURNAL, api_key=self.s.arete_api_key
        )
        self.gpu = gpu_name()
        self._busy = threading.Lock()

    def capture_meta(self, width: int, height: int) -> dict:
        return {
            "encoder": "h264_nvenc",
            "gpu": self.gpu,
            "capture_api": "ddagrab (Desktop Duplication)",
            "audio": self.ring.audio_device or "none",
            "width": width,
            "height": height,
            "fps": self.s.capture_fps,
            "bitrate": self.s.capture_bitrate,
            "segment_seconds": self.s.segment_seconds,
            "buffer_seconds": self.s.buffer_seconds,
            "output_idx": self.s.ddagrab_output_idx,
        }

    def make_clip(self) -> str | None:
        """Flush the buffer and upload. Returns the share URL."""
        if not self._busy.acquire(blocking=False):
            print("  ... already making a clip, ignoring")
            return None
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

            url = self.uploader.submit(
                result, captured_at, self.capture_meta(result.width, result.height)
            )
            print(f"  uploaded in {time.perf_counter() - remuxed:.2f}s")
            print(f"  -> {url}   (time to link: {time.perf_counter() - started:.2f}s)")
            if copy_to_clipboard(url):
                print("  copied to clipboard")
            return url
        except ClipError as exc:
            print(f"  clip failed: {exc}")
        except (UploadError, OSError) as exc:
            print(f"  upload failed, kept in the journal for retry: {exc}")
        finally:
            self._busy.release()
        return None

    def start_buffer(self) -> None:
        print(f"Starting ring buffer: {self.s.buffer_seconds}s window, "
              f"{self.s.segment_seconds}s segments, output_idx={self.s.ddagrab_output_idx}")
        self.ring.start()
        time.sleep(self.s.segment_seconds + 1.5)
        if not self.ring.is_running():
            raise SystemExit(
                f"capture process exited immediately.\n{self.ring.tail_log()}"
            )
        audio = self.ring.audio_device
        print(f"Capturing on {self.gpu} via h264_nvenc")
        if audio:
            print(f"Desktop audio: {audio}")
        else:
            print(
                "Desktop audio: none found, clips will be silent. "
                "Install a loopback capture device, or set AUDIO_DEVICE."
            )

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


if __name__ == "__main__":
    sys.exit(main())
