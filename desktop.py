"""Arete as a desktop app.

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
import ctypes
import os
import socket
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from paths import data_dir

ROOT = Path(__file__).resolve().parent
# Beside the config, not the executable: a one-file build unpacks to a temp
# directory that is deleted on exit, taking the log with it.
LOG_PATH = data_dir() / "arete.log"

# Launched via pythonw.exe there is no console, so sys.stdout is None and any
# print() anywhere in the process raises. Redirect before importing anything
# that might log.
if sys.stdout is None or sys.stderr is None:
    _log = LOG_PATH.open("a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from urllib.parse import quote, urlparse  # noqa: E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import webview  # noqa: E402

from branding import APP_ID, APP_NAME  # noqa: E402
from capture.daemon import Daemon  # noqa: E402
from capture.hotkey import pump, stop as hotkey_stop  # noqa: E402
from paths import bundle_dir, config_file, is_configured  # noqa: E402
from services import StorageServer, Tunnels, update_config  # noqa: E402
from setup import SetupApi, setup_page  # noqa: E402
from tray import Tray  # noqa: E402

# Loopback by default. Setting BIND_HOST=0.0.0.0 makes share links work for
# anyone on the same network, which is the difference between a link that plays
# on your machine and one you can actually send someone. There is no auth, so
# only do that on a network you trust: every clip becomes readable, and
# deletable, by anyone who can reach the port.
HOST = os.environ.get("BIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
# Where the window points. Distinct from HOST, because 0.0.0.0 is a bind
# address and not somewhere anything can navigate to.
UI_HOST = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
DIST = bundle_dir() / "frontend" / "dist"
ICON_PATH = bundle_dir() / "assets" / "arete.ico"


def ensure_local_account() -> None:
    """Give this machine an account on its own server, if it has none.

    A solo install hosts its own clips, so needing a key from someone would be
    absurd. Existing users are never touched: only the hash of a key is stored,
    so an account cannot be handed back its key, and rotating someone else's on
    a host others have joined would lock them out.
    """
    from sqlalchemy import select

    from server.auth import generate_key, hash_key
    from server.db import SessionLocal
    from server.ids import uuid7
    from server.migrate import ensure_schema
    from server.models import User

    if capture_config().arete_api_key.strip():
        return

    ensure_schema()
    session = SessionLocal()
    try:
        taken = {handle for handle in session.scalars(select(User.handle)).all()}
        handle = "me"
        suffix = 2
        while handle in taken:
            handle = f"me-{suffix}"
            suffix += 1
        key = generate_key()
        session.add(User(id=uuid7(), handle=handle, api_key_hash=hash_key(key)))
        session.commit()
    finally:
        session.close()

    update_config(ARETE_API_KEY=key)
    print(f"  created a local account ({handle}) for this machine")


def host_flag(name: str, default: bool) -> bool:
    """Read a host switch straight from the config file.

    Deliberately not through either settings class: these decide what happens
    before any server module is imported, and importing one early would cache
    settings that the tunnels are about to rewrite.
    """
    value = os.environ.get(name)
    if value is None:
        path = config_file()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith(f"{name}="):
                    value = line.split("=", 1)[1].strip()
                    break
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def capture_config():
    """Read settings fresh.

    Deliberately not cached at import: setup writes the config file while the
    process is already running, and everything downstream has to see it.
    """
    from capture.config import CaptureSettings, get_capture_settings

    get_capture_settings.cache_clear()
    CaptureSettings.model_config["env_file"] = str(config_file())
    return get_capture_settings()


def api_base() -> str:
    return capture_config().api_base_url.rstrip("/")


def is_host() -> bool:
    """Whether this machine runs the API, or only talks to one.

    A second person points API_BASE_URL at the host and should not start a
    server of their own: their clips belong in the shared database. Deriving
    the mode from where the API lives keeps it from being a flag to forget.
    """
    return urlparse(api_base()).hostname in {"localhost", "127.0.0.1", "::1", None}


def app_url() -> str:
    # The key rides in the fragment, which is never sent to the server, so it
    # stays out of access logs while still reaching the page before its first
    # request.
    key = capture_config().arete_api_key
    fragment = f"#k={quote(key)}" if key else ""
    base = f"http://{UI_HOST}:{PORT}" if is_host() else api_base()
    return f"{base}/app/{fragment}"


def claim_taskbar_identity() -> None:
    """Tell the shell this process is its own app, not an instance of Python.

    Without an explicit AppUserModelID, Windows derives one from the running
    executable, so the taskbar button inherits pythonw.exe: the Python icon,
    and grouping with any other Python window. It has to be set before the
    first window exists, because the shell reads it when the button is made.
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass  # pre-Win7 or a stubbed shell32; the window still works


def _process_windows() -> list[int]:
    """Top-level visible windows belonging to this process."""
    user32 = ctypes.windll.user32
    found: list[int] = []
    mine = ctypes.windll.kernel32.GetCurrentProcessId()

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _param) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == mine and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return found


def apply_window_icon(path: Path) -> bool:
    """Put the app mark on the taskbar button, the title bar and Alt-Tab.

    pywebview hands its window straight to WebView2 and never sets an icon, so
    the window inherits the class icon of the executable behind it. WM_SETICON
    overrides that per window, which is what makes the icon correct even in a
    development run started from pythonw.exe rather than the built launcher.
    """
    if not path.is_file():
        return False
    user32 = ctypes.windll.user32
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x00000010
    WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
    SM_CXSMICON, SM_CYSMICON, SM_CXICON, SM_CYICON = 49, 50, 11, 12

    def load(cx: int, cy: int) -> int:
        return user32.LoadImageW(
            None, str(path), IMAGE_ICON,
            user32.GetSystemMetrics(cx), user32.GetSystemMetrics(cy),
            LR_LOADFROMFILE,
        )

    # Two sizes: the small one is the title bar and Alt-Tab, the big one is the
    # taskbar. Asking for the right size up front picks the matching image out
    # of the .ico instead of letting the shell rescale a neighbour.
    small, big = load(SM_CXSMICON, SM_CYSMICON), load(SM_CXICON, SM_CYICON)
    if not small and not big:
        return False

    windows = _process_windows()
    for hwnd in windows:
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small or big)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big or small)
    return bool(windows)


def port_is_taken(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def wait_for_health(timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://{UI_HOST}:{PORT}/healthz", timeout=1.0).status_code == 200:
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
        # Imported here, not at module load, for two reasons. Ordering: the
        # tunnels rewrite the public addresses this module reads at import.
        # Packaging: naming it as the string "server.main:app" hid it from
        # PyInstaller's analysis, so the module was never bundled and the
        # packaged app failed with "Could not import module".
        from server.main import app as asgi_app

        config = uvicorn.Config(
            asgi_app,
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


# Only the keys settings offers, so the log names what the user chose.
_KEY_NAMES = {
    0x75: "F6", 0x76: "F7", 0x77: "F8", 0x78: "F9",
    0x79: "F10", 0x7A: "F11", 0x7B: "F12",
}


class CaptureService:
    """Ring buffer plus hotkey pump, on their own thread.

    RegisterHotKey delivers WM_HOTKEY to the thread that registered it, so the
    message loop has to live here rather than on the UI thread.
    """

    # How often the watchdog checks, and how long it gives a restarted ffmpeg
    # to either come up or fall over again. Class attributes so tests can shrink
    # them instead of sleeping through real intervals.
    poll_seconds = 3.0
    settle_seconds = 2.5
    # Segments land every couple of seconds, so silence for several times that
    # means production has stopped even if the process is still alive.
    stall_seconds = 8.0

    def __init__(
        self,
        on_clip: Callable[[dict], None] | None = None,
        on_health: Callable[[bool, str], None] | None = None,
    ) -> None:
        self.daemon: Daemon | None = None
        self.error: str | None = None
        self.ready = threading.Event()
        self._on_clip = on_clip
        self._on_health = on_health
        # Distinguishes a deliberate shutdown from the capture process dying,
        # so the watchdog does not fight teardown by restarting ffmpeg.
        self._stopping = threading.Event()
        self._pump_thread_id = 0
        self.thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self.watchdog = threading.Thread(
            target=self._watch, name="capture-watchdog", daemon=True
        )

    def _run(self) -> None:
        try:
            self.daemon = Daemon()
            self.daemon.drain_journal()
            self.daemon.start_buffer()
            self.ready.set()
            self.watchdog.start()

            def armed(thread_id: int) -> None:
                # Only now is the key actually bound. Announcing it earlier
                # claimed success the code had not earned, which made a failed
                # binding look like a hotkey that simply did nothing.
                self._pump_thread_id = thread_id
                key = self.daemon.s.hotkey_vk
                name = _KEY_NAMES.get(key, f"vk {key:#04x}")
                print(f"Hotkey armed: {name} clips the last {self.daemon.s.clip_seconds}s")

            pump(self.daemon.s.hotkey_vk, self._on_hotkey, on_ready=armed)
        except Exception as exc:  # noqa: BLE001
            # Capture can fail for reasons the app should survive: no NVENC, a
            # missing ffmpeg, a display index that no longer exists, or the
            # hotkey already owned by another program. The library still works
            # without it, so record the reason and leave the window running.
            self.error = str(exc)
            print(f"[capture] disabled: {exc}")

    def _health(self, healthy: bool, message: str) -> None:
        print(f"[capture] {message}")
        if self._on_health is not None:
            self._on_health(healthy, message)

    def _watch(self) -> None:
        """Restart the ring buffer if it dies.

        A GPU driver reset, a display being unplugged or ffmpeg being killed all
        end capture while the app carries on looking perfectly healthy. Stopping
        silently is the worst thing this app can do: you find out by pressing F9
        after the moment you wanted and getting nothing.

        Restarting clears the buffer, because segments from before and after a
        crash would splice together with a timestamp discontinuity. The history
        is gone either way, so say so rather than hiding it.
        """
        failures = 0
        while not self._stopping.wait(self.poll_seconds):
            if self.daemon is None:
                continue
            ring = self.daemon.ring
            alive = ring.is_running()
            stalled = alive and ring.is_stalled(self.stall_seconds)
            if alive and not stalled:
                failures = 0
                continue

            failures += 1
            reason = "stalled" if stalled else "stopped"
            self._health(False, f"Recording {reason}. Restarting.")
            try:
                # A wedged process has to be killed before it can be replaced;
                # start() alone would see it alive and decline to do anything.
                ring.stop()
                ring.start()
            except OSError as exc:
                print(f"[capture] restart failed: {exc}")

            # Give ffmpeg a moment to either come up or fall over again.
            if self._stopping.wait(self.settle_seconds):
                return
            if ring.is_running():
                self._health(True, "Recording resumed. Buffer history was lost.")
                failures = 0
            elif failures >= 3:
                self.error = "capture stopped and could not be restarted"
                self._health(False, "Recording stopped and will not restart.")
                return

    def _make_clip(self) -> None:
        if self.daemon is None:
            return
        result = self.daemon.make_clip()
        if self._on_clip is not None:
            self._on_clip(result)

    def trigger(self) -> None:
        """Take a clip now. Used by both the hotkey and the tray menu."""
        threading.Thread(target=self._make_clip, daemon=True).start()

    def _on_hotkey(self) -> None:
        self.trigger()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stopping.set()
        # Release the hotkey too, or a rebind leaves the old key still firing.
        hotkey_stop(self._pump_thread_id)
        if self.daemon is not None:
            self.daemon.ring.stop()


def preflight() -> bool:
    """Check the services the app depends on before opening a window.

    Self-hosting means Postgres and the object store are separate processes that
    can simply be off. Finding that out from an empty library and a stack trace
    in a log file is worse than being told which one is not running.
    """
    from sqlalchemy import text as sql_text

    from server.config import get_settings
    from server.db import engine
    from server.storage import get_storage

    settings = get_settings()
    ok = True

    try:
        with engine.connect() as conn:
            conn.execute(sql_text("select 1"))
    except Exception as exc:  # noqa: BLE001
        target = settings.database_url.split("@")[-1]
        print(f"Database unreachable ({target}): {type(exc).__name__}")
        if "postgresql" in settings.database_url:
            print("  Start it with:  net start postgresql-x64-18   (needs admin)")
        ok = False

    if settings.storage_backend in {"s3", "r2"}:
        try:
            get_storage().size_of("preflight-probe-that-does-not-exist")
        except Exception as exc:  # noqa: BLE001
            print(f"Object storage unreachable ({settings.s3_endpoint_url}): "
                  f"{type(exc).__name__}")
            print(r"  Start it with:  scripts\start-storage.bat")
            ok = False

    return ok


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME.lower())
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

    # Packaged, these are the answers to most "where did it put that" and
    # "why is it not reading my settings" questions, and they cost one line.
    print(f"{APP_NAME} starting")
    print(f"  config : {config_file()}")
    print(f"  data   : {LOG_PATH.parent}")
    print(f"  buffer : {capture_config().ring_buffer_dir}")

    # Before any window exists, or the taskbar button is already Python's.
    claim_taskbar_identity()

    api: ApiServer | None = None
    storage: StorageServer | None = None
    tunnels: Tunnels | None = None
    configured = is_configured()

    def start_backend() -> bool:
        """Bring up whatever this machine is responsible for.

        Called before the window when already configured, and again from the
        setup screen once settings exist, because until then there is nothing
        to connect to and no way to know which mode this machine is in.
        """
        nonlocal api
        if is_host() and not preflight():
            return False
        if is_host():
            return start_host()
        return check_client()

    def start_host() -> bool:
        nonlocal api, storage, tunnels

        if not DIST.is_dir():
            print("frontend/dist is missing. Build it:  cd frontend && npm run build")
            return False
        if port_is_taken(UI_HOST, PORT):
            print(
                f"Port {PORT} is already in use. Another {APP_NAME} or a uvicorn "
                "started from a terminal is probably still running."
            )
            return False

        ensure_local_account()

        # A code to hand out, so a new machine needs one string rather than a
        # key issued by hand. Generated once and kept, because changing it
        # would silently stop anyone mid-setup.
        from server.config import Settings as _Settings

        if not _Settings().invite_code.strip():
            from server.auth import generate_key

            update_config(INVITE_CODE=generate_key().replace("arete_", "join_")[:20])

        # Storage first: the API hands out upload URLs pointing at it, and the
        # tunnel that publishes it has to have something to publish.
        if host_flag("MANAGE_STORAGE", True):
            from server.config import Settings

            settings = Settings()
            storage = StorageServer(
                settings.s3_access_key_id,
                settings.s3_secret_access_key,
                directory=settings.minio_data_dir or None,
            )
            if settings.storage_backend in {"s3", "r2"} and settings.s3_endpoint_url:
                print("Starting object storage...")
                if storage.start():
                    print(f"  storage ready{' (already running)' if storage.adopted else ''}")
                else:
                    print("  storage did not start; clips will fail to upload")

        # Tunnels before the API, because they rewrite the public addresses the
        # API bakes into every share page and upload URL when it imports.
        if host_flag("MANAGE_TUNNEL", True):
            print("Opening tunnels...")
            tunnels = Tunnels(api_port=PORT)
            addresses = tunnels.start()
            if addresses:
                print(f"  public address: {addresses[0]}")
            else:
                print("  no tunnel; links will only work on this machine")

        # The addresses just changed, so anything built from the old ones is
        # wrong. Rebuild, then make sure the bucket exists and is readable.
        if host_flag("MANAGE_STORAGE", True):
            from server.config import get_settings
            from server.storage import S3Storage, get_storage, reset_storage

            get_settings.cache_clear()
            reset_storage()
            try:
                backend = get_storage()
                if isinstance(backend, S3Storage):
                    backend.ensure_bucket(public_read=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  could not prepare the bucket: {exc}")

        api = ApiServer(verbose=args.verbose)
        api.start()
        if not wait_for_health():
            print(f"The API did not come up on port {PORT}. See {LOG_PATH}.")
            return False

        from server.config import Settings as _Fresh

        current = _Fresh()
        print()
        print("To add another machine, give it these two things:")
        print(f"  server : {current.public_base_url}")
        print(f"  invite : {current.invite_code}")
        print()
        return True

    def check_client() -> bool:
        # Client mode: someone else hosts the API and the database, so this
        # machine starts no server of its own. Both checks fail loudly here
        # rather than opening a window onto an unreachable host, where the
        # symptom would be an empty library with no explanation.
        print(f"Using the API at {api_base()}")
        if not capture_config().arete_api_key:
            print(
                "No ARETE_API_KEY set. Ask whoever runs the host "
                "to issue one: python -m tools.add_user --handle <name>"
            )
            return False
        try:
            httpx.get(f"{api_base()}/healthz", timeout=8.0).raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Cannot reach {api_base()}: {exc}")
            return False
        return True

    if configured and not start_backend():
        return 1

    tray: Tray | None = None
    # Set once the tray Quit item runs, so the closing handler stops hiding the
    # window and lets it actually close.
    quitting = threading.Event()
    hinted = threading.Event()

    def on_clip_done(result: dict) -> None:
        if tray is None:
            return
        status = (result or {}).get("status")
        if status == "uploaded":
            tray.notify(f"Clip saved and link copied: {result['url']}")
        elif status == "held":
            tray.notify("Clip saved. Open Arete to generate a link.")
        elif status == "busy":
            return
        else:
            tray.notify("Clip failed. See the log for the reason.")

    def on_health(healthy: bool, message: str) -> None:
        if tray is None:
            return
        tray.set_recording(healthy)
        tray.notify(message)

    capture = CaptureService(on_clip=on_clip_done, on_health=on_health)
    if configured and not args.no_capture:
        capture.start()

    # A mutable holder: the capture service is replaced when settings change,
    # and the tray callbacks close over this rather than a stale instance.
    services = {"capture": capture}

    def apply_settings() -> None:
        """Re-read config and rebuild capture, then return to the app."""
        old_service = services["capture"]
        old_service.stop()
        fresh = CaptureService(on_clip=on_clip_done, on_health=on_health)
        services["capture"] = fresh
        if not args.no_capture:
            fresh.start()
        window.resize(1280, 820)
        window.load_url(app_url())

    def open_settings() -> None:
        window.load_url(setup_page())
        window.show()
        window.restore()

    setup_api = SetupApi(
        on_saved=(apply_settings if configured else None) or (lambda: None),
        on_skipped=lambda: window.load_url(app_url()),
        # Resolved on each call: the capture service is replaced when settings
        # change, and its uploader with it.
        uploader_factory=lambda: services["capture"].daemon.uploader,
    )

    if configured:
        window = webview.create_window(
            APP_NAME,
            app_url(),
            width=1280,
            height=820,
            min_size=(880, 560),
            background_color="#0b0e14",
            js_api=setup_api,
        )
    else:
        # Nothing is configured yet, so there is no server to point a window at
        # and nothing to record. Setup runs in this same window and navigates
        # to the app when it is done: pywebview runs one GUI loop per process,
        # so a second window is not an option.
        def finish_setup() -> None:
            if not start_backend():
                print("Settings saved, but the service did not start.")
                return
            if not args.no_capture:
                capture.start()
            window.resize(1280, 820)
            window.load_url(app_url())

        def abandon_setup() -> None:
            quitting.set()
            window.destroy()

        setup_api._on_saved = finish_setup
        setup_api._on_skipped = abandon_setup
        window = webview.create_window(
            f"{APP_NAME} setup",
            setup_page(),
            js_api=setup_api,
            width=760,
            height=760,
            background_color="#0b0e14",
        )

    if not args.no_tray and configured:

        def show_window() -> None:
            window.show()
            window.restore()

        def quit_app() -> None:
            quitting.set()
            # Stop the recorder before tearing down the UI. If window.destroy()
            # ever stalls, the finally block never runs, and a stranded ffmpeg
            # would keep writing segments with nothing left to flush them.
            services["capture"].stop()
            window.destroy()

        tray = Tray(
            on_open=show_window,
            on_clip=lambda: services["capture"].trigger(),
            on_quit=quit_app,
            on_settings=open_settings,
        )

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

    def dress_window() -> None:
        """Runs once the GUI loop is up and the window has a handle.

        The handle does not exist while the window is only a pywebview object,
        so this cannot happen at create_window time; pywebview calls it after
        the native window is real.
        """
        for _ in range(20):
            if apply_window_icon(ICON_PATH):
                return
            time.sleep(0.1)
        print(f"[icon] no window took the icon from {ICON_PATH}")

    def reflect_capture_state() -> None:
        """The tray icon is the only place capture failure is visible once the
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
        webview.start(dress_window, debug=args.verbose)
    finally:
        if tray is not None:
            tray.stop()
        services["capture"].stop()
        if api is not None:
            api.stop()
        # Reverse order: stop publishing before removing what was published.
        if tunnels is not None:
            tunnels.stop()
        if storage is not None:
            storage.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
