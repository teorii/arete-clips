"""Clipper as a desktop app.

Runs the API, the capture daemon and the window in a single process, so there
is nothing to start in a terminal and no URL to type. The window is a native
WebView2 frame around the same React app the server already serves.

Closing the window stops recording. Minimise it instead to keep the ring
buffer running while you play.

    python desktop.py                 window plus capture
    python desktop.py --no-capture    window only, for UI work
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "clipper.log"

# Launched via pythonw.exe there is no console, so sys.stdout is None and any
# print() anywhere in the process raises. Redirect before importing anything
# that might log.
if sys.stdout is None or sys.stderr is None:
    _log = LOG_PATH.open("a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import webview  # noqa: E402

from capture.daemon import Daemon  # noqa: E402
from capture.hotkey import pump  # noqa: E402
from tray import Tray  # noqa: E402

HOST = "127.0.0.1"
PORT = 8000
DIST = ROOT / "frontend" / "dist"
APP_URL = f"http://{HOST}:{PORT}/app/"


def port_is_taken(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def wait_for_health(timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://{HOST}:{PORT}/healthz", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return False


class ApiServer:
    """uvicorn on a worker thread.

    uvicorn already declines to install signal handlers when it is not on the
    main thread, which is what lets the webview own the main loop.
    """

    def __init__(self, verbose: bool = False) -> None:
        config = uvicorn.Config(
            "server.main:app",
            host=HOST,
            port=PORT,
            log_level="info" if verbose else "warning",
            access_log=verbose,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="api", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


class CaptureService:
    """Ring buffer plus hotkey pump, on their own thread.

    RegisterHotKey delivers WM_HOTKEY to the thread that registered it, so the
    message loop has to live here rather than on the UI thread.
    """

    def __init__(self, on_clip: Callable[[str | None], None] | None = None) -> None:
        self.daemon: Daemon | None = None
        self.error: str | None = None
        self.ready = threading.Event()
        self._on_clip = on_clip
        self.thread = threading.Thread(target=self._run, name="capture", daemon=True)

    def _run(self) -> None:
        try:
            self.daemon = Daemon()
            self.daemon.drain_journal()
            self.daemon.start_buffer()
            print(f"Hotkey armed: press F9 for the last {self.daemon.s.clip_seconds}s")
            self.ready.set()
            pump(self.daemon.s.hotkey_vk, self._on_hotkey)
        except Exception as exc:  # noqa: BLE001
            # Capture can fail for reasons the app should survive: no NVENC, a
            # missing ffmpeg, a display index that no longer exists, or the
            # hotkey already owned by another program. The library still works
            # without it, so record the reason and leave the window running.
            self.error = str(exc)
            print(f"[capture] disabled: {exc}")

    def _make_clip(self) -> None:
        if self.daemon is None:
            return
        url = self.daemon.make_clip()
        if self._on_clip is not None:
            self._on_clip(url)

    def trigger(self) -> None:
        """Take a clip now. Used by both the hotkey and the tray menu."""
        threading.Thread(target=self._make_clip, daemon=True).start()

    def _on_hotkey(self) -> None:
        self.trigger()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        if self.daemon is not None:
            self.daemon.ring.stop()


def main() -> int:
    parser = argparse.ArgumentParser(prog="clipper")
    parser.add_argument(
        "--no-capture", action="store_true", help="window only, do not record"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="log every HTTP request"
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="no tray icon; closing the window then quits",
    )
    args = parser.parse_args()

    if not DIST.is_dir():
        print("frontend/dist is missing. Build it first:  cd frontend && npm run build")
        return 1

    if port_is_taken(HOST, PORT):
        print(
            f"Port {PORT} is already in use. Another Clipper or a uvicorn started "
            "from a terminal is probably still running."
        )
        return 1

    api = ApiServer(verbose=args.verbose)
    api.start()
    if not wait_for_health():
        print(f"The API did not come up on port {PORT}. See {LOG_PATH}.")
        return 1

    tray: Tray | None = None
    # Set once the tray Quit item runs, so the closing handler stops hiding the
    # window and lets it actually close.
    quitting = threading.Event()
    hinted = threading.Event()

    def on_clip_done(url: str | None) -> None:
        if tray is None:
            return
        if url:
            tray.notify(f"Clip saved and link copied: {url}")
        else:
            tray.notify("Clip failed. See the log for the reason.")

    capture = CaptureService(on_clip=on_clip_done)
    if not args.no_capture:
        capture.start()

    window = webview.create_window(
        "Clipper",
        APP_URL,
        width=1280,
        height=820,
        min_size=(880, 560),
        background_color="#0b0e14",
    )

    if not args.no_tray:

        def show_window() -> None:
            window.show()
            window.restore()

        def quit_app() -> None:
            quitting.set()
            # Stop the recorder before tearing down the UI. If window.destroy()
            # ever stalls, the finally block never runs, and a stranded ffmpeg
            # would keep writing segments with nothing left to flush them.
            capture.stop()
            window.destroy()

        tray = Tray(on_open=show_window, on_clip=capture.trigger, on_quit=quit_app)

        def on_closing() -> bool:
            """Hide instead of quitting.

            Closing the window while a game is running should not stop the ring
            buffer, which is the whole point of a background recorder. Quit is
            an explicit choice from the tray menu.
            """
            if quitting.is_set():
                return True
            window.hide()
            if not hinted.is_set():
                hinted.set()
                tray.notify("Still recording. Reopen or quit from the tray icon.")
            return False

        window.events.closing += on_closing
        tray.start()

    def reflect_capture_state() -> None:
        """The tray dot is the only place capture failure is visible once the
        window is hidden, so make sure it tells the truth."""
        if tray is None:
            return
        if args.no_capture:
            tray.set_recording(False)
            return
        capture.ready.wait(timeout=30)
        tray.set_recording(capture.error is None and capture.ready.is_set())

    threading.Thread(target=reflect_capture_state, daemon=True).start()

    try:
        webview.start(debug=args.verbose)
    finally:
        if tray is not None:
            tray.stop()
        capture.stop()
        api.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
