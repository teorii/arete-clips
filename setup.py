"""First-run setup.

Opens before the main window on a machine that has not been configured, and
answers the three questions the app cannot guess: where the server is, which
key identifies this person, and which display the game is on. It also checks
for a usable encoder up front, because finding out there isn't one by pressing
the hotkey and getting nothing is a bad way to learn it.

The setup page and the app share a single window. pywebview runs one GUI loop
per process, so finishing setup navigates that window rather than opening a
second one.
"""

from __future__ import annotations

import base64
import re
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from paths import bundle_dir, config_file
from problems import warn


def _thumbnail_data_uri(path: str | None) -> str | None:
    """Inline the poster frame.

    A held clip only exists on this machine, so its thumbnail has no URL to
    serve it from. Inlining is what lets it look like any other card in the
    library instead of an empty box, and a poster frame is small enough that
    passing a few through the bridge costs nothing.
    """
    if not path:
        return None
    file = Path(path)
    if not file.exists():
        return None
    try:
        encoded = base64.b64encode(file.read_bytes()).decode("ascii")
    except OSError as exc:
        warn("poster", exc, "this clip will show an empty thumbnail")
        return None
    return f"data:image/jpeg;base64,{encoded}"


def setup_page() -> str:
    return str(bundle_dir() / "ui" / "setup.html")


def settings_page() -> str:
    """Changing settings is not first-run setup, and does not read like it.

    Setup asks where clips should live and whether the machine can encode at
    all, questions with one answer per install. Settings changes what is
    already working, so it drops the numbered steps, the hardware check and the
    option to skip, none of which mean anything on the second visit.
    """
    return str(bundle_dir() / "ui" / "settings.html")


def write_config(values: dict[str, object]) -> Path:
    """Merge settings into the config file, leaving anything else alone.

    Merged rather than rewritten: in development this file is the project .env,
    which holds database and storage settings that setup knows nothing about
    and must not destroy.
    """
    path = config_file()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    seconds = int(values["seconds"])
    # Hosting points at this machine. The account and its key are created by the
    # app on first start, so there is nothing for anyone to be given or type.
    hosting = str(values.get("mode", "solo")) == "solo"
    updates = {
        "API_BASE_URL": "http://localhost:8000" if hosting else str(values["url"]).rstrip("/"),
        "DDAGRAB_OUTPUT_IDX": str(int(values["display"])),
        "CLIP_SECONDS": str(seconds),
        # Enough headroom that a clip is never cut short by the ring wrapping.
        "BUFFER_SECONDS": str(max(60, seconds * 2)),
        "HOTKEY_VK": str(values.get("hotkey", "0x78")),
        # What tells the app not to ask again. Hosting installs have no key at
        # this point, so a key cannot be the signal.
        "SETUP_COMPLETE": "1",
    }
    # Only when the page offers it. Setup does not ask, so its save must not
    # write a default over whatever the config already says.
    if values.get("bitrate"):
        updates["CAPTURE_BITRATE"] = str(values["bitrate"])
    if values.get("audioOffsetMs") is not None:
        updates["AUDIO_OFFSET_MS"] = str(int(values["audioOffsetMs"]))

    # A hosting install issues its own key on first start, and this file is
    # rewritten every time the source or a setting changes. Blanking the key
    # here therefore un-authenticated the machine mid-session: the clip you had
    # just taken was refused, and it only worked again after a restart minted a
    # new account. Written once, when there is nothing to lose.
    if hosting:
        if not re.search(r"^ARETE_API_KEY=.+$", existing, flags=re.M):
            updates["ARETE_API_KEY"] = ""
    else:
        updates["ARETE_API_KEY"] = str(values["key"]).strip()

    text = existing
    for key, value in updates.items():
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", text, flags=re.M):
            text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    return path


def _later(action: Callable[[], None]) -> None:
    """Run something that navigates the window, after this call has answered.

    pywebview resolves an api call by evaluating JavaScript that looks up a
    callback the page registered. Navigating from inside the call throws that
    registry away, so the resolve lands on a page that has never heard of it:

        TypeError: window.pywebview._returnValuesCallbacks.save... is not
        a function

    The call then never settles, and the bridge is wedged for everything after
    it. Saving settings did exactly this, which is why generating a link stopped
    working once you had visited the settings screen.
    """

    def run() -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001
            warn("navigation", exc, "the window stayed where it was")

    threading.Timer(0.15, run).start()


def _preview_url(mp4_path: str) -> str:
    """A URL the library can play a held clip from.

    Held clips are files on this machine that the server has never heard of, so
    deciding whether one deserves a link meant guessing from a thumbnail. Empty
    when this install is a client of another Arete: the file is here and the
    server is not.
    """
    import time

    from capture.config import get_capture_settings
    from server.storage import sign_held

    try:
        # Hosting is decided by where the client sends clips, not by the public
        # address: a host with a tunnel still serves this page itself. Relative,
        # so it resolves against whichever of the two the window is showing.
        if not _is_local(get_capture_settings().api_base_url):
            return ""
        name = Path(mp4_path).name
        expires = int(time.time()) + 12 * 3600
        return (
            f"/api/held/{quote(name)}"
            f"?expires={expires}&sig={sign_held(name, expires)}"
        )
    except Exception as exc:  # noqa: BLE001
        warn("preview link", exc, "held clips cannot be played before sharing")
        return ""


def _is_local(url: str) -> bool:
    """Whether an address points at this machine, which is what makes an
    install a host rather than a client of someone else's."""
    return bool(re.search(r"localhost|127\.0\.0\.1", url))


class AppBridge:
    """Everything the pages can call, exposed as `pywebview.api`.

    Attached to the one window the app owns, so both the setup page and the
    clip library reach it. The library needs it because held clips live on this
    machine and the server has never heard of them: a Generate link button has
    to reach the client, not the API.
    """

    def __init__(
        self,
        uploader_factory=None,
        open_settings=None,
        close_settings=None,
        restart_capture=None,
    ):
        self._uploader_factory = uploader_factory
        self._open_settings = open_settings
        self._close_settings = close_settings
        self._restart_capture = restart_capture

    def close_settings(self) -> dict:
        """Leave settings without saving. Not the same as skipping setup."""
        if self._close_settings is None:
            return {"ok": False, "message": "nothing to return to"}
        _later(self._close_settings)
        return {"ok": True}

    def open_settings(self) -> dict:
        """Show the settings screen.

        Reachable from the window as well as the tray: Windows files new tray
        icons under the overflow chevron, so a menu there is not somewhere
        anyone finds a setting.
        """
        if self._open_settings is None:
            return {"ok": False, "message": "settings are not available here"}
        _later(self._open_settings)
        return {"ok": True}

    def _uploader(self):
        if self._uploader_factory is None:
            raise RuntimeError("no uploader is available yet")
        return self._uploader_factory()

    def preferences(self) -> dict:
        """Current preferences, for the settings screen and the library."""
        from dataclasses import asdict

        from preferences import load

        return asdict(load())

    def save_preferences(self, values: dict) -> dict:
        from dataclasses import asdict

        from preferences import update

        try:
            return {"ok": True, "preferences": asdict(update(values or {}))}
        except Exception as exc:  # noqa: BLE001
            warn("preferences", exc, "the change was not saved")
            return {"ok": False, "message": str(exc)}

    def held_clips(self) -> list[dict]:
        """Clips captured on this machine that have no link yet."""
        try:
            entries = self._uploader().waiting()
        except Exception as exc:  # noqa: BLE001
            warn("held clips", exc, "the library will not show clips waiting for a link")
            return []
        return [
            {
                "path": entry["mp4_path"],
                "capturedAt": entry.get("captured_at"),
                "durationMs": entry.get("duration_ms", 0),
                "bytes": entry.get("source_bytes", 0),
                "title": entry.get("title"),
                "thumb": _thumbnail_data_uri(entry.get("thumb_path")),
                "previewUrl": _preview_url(entry["mp4_path"]),
            }
            for entry in entries
        ]

    def generate_link(self, path: str) -> dict:
        try:
            return {"ok": True, "url": self._uploader().publish(path)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def rename_clip(self, path: str, title: str) -> dict:
        try:
            found = self._uploader().rename(path, title)
            return {"ok": found, "message": "" if found else "clip is no longer held"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def discard_all_clips(self) -> dict:
        try:
            return {"ok": True, "removed": self._uploader().discard_all()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def discard_clip(self, path: str) -> dict:
        try:
            self._uploader().discard(path)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}


class SetupApi(AppBridge):
    """Bridge exposed to the setup page as `pywebview.api`."""

    def __init__(
        self,
        on_saved: Callable[[], None],
        on_skipped: Callable[[], None],
        uploader_factory=None,
        open_settings=None,
        close_settings=None,
        restart_capture=None,
    ):
        super().__init__(
            uploader_factory, open_settings, close_settings, restart_capture
        )
        self._on_saved = on_saved
        self._on_skipped = on_skipped
        self.saved = False

    def probe(self) -> dict:
        from capture.probe import gpu_name, has_nvenc, list_displays
        from capture.windows import visible_windows

        try:
            programs = visible_windows()
        except OSError:
            programs = []

        return {
            "encoder_ok": has_nvenc(),
            "gpu": gpu_name(),
            "displays": list_displays(),
            # Capture is per display, so a program is just a friendlier way of
            # naming one. Resolved here rather than in the page.
            "programs": programs,
            "current": self.current(),
        }

    def current(self) -> dict:
        """Existing settings, so the screen opens on what is configured."""
        from capture.config import CaptureSettings, get_capture_settings

        get_capture_settings.cache_clear()
        CaptureSettings.model_config["env_file"] = str(config_file())
        settings = get_capture_settings()
        return {
            "url": settings.api_base_url,
            "key": settings.arete_api_key,
            "display": settings.ddagrab_output_idx,
            "seconds": settings.clip_seconds,
            "hotkey": f"0x{settings.hotkey_vk:02X}",
            "bitrate": settings.capture_bitrate,
            "audioOffsetMs": settings.audio_offset_ms,
        }

    def sources(self) -> dict:
        """What can be recorded, without the hardware check.

        probe() also asks whether the encoder works, which spawns ffmpeg. That
        is worth doing once during setup and not every time a dropdown opens.
        """
        from capture.probe import list_displays
        from capture.windows import monitors

        # ddagrab counts displays from zero in enumeration order; Windows
        # numbers them from one in an order of its own, and on a multi-monitor
        # desk the two disagree. Label with the number Display Settings shows,
        # and keep the ddagrab index as the value.
        try:
            numbers = [m.get("number") for m in monitors()]
        except OSError as exc:
            warn("display names", exc, "displays will be numbered as captured")
            numbers = []

        # A number and a resolution, and nothing else. Listing the programs on
        # each screen was meant to help you recognise it and did the opposite:
        # the list churns as windows move, so the same display read differently
        # from one look to the next. A resolution does not move.
        entries = []
        for display in list_displays():
            index = display["index"]
            shown = (
                numbers[index]
                if index < len(numbers) and numbers[index]
                else index + 1
            )
            entries.append({
                **display,
                "label": f"Display {shown} ({display['width']}x{display['height']})",
            })

        return {"displays": entries, "current": self.current()["display"]}

    def set_source(self, display: int) -> dict:
        """Record a different display, without leaving the library to do it.

        Everything else in the config is carried through: this is one setting,
        not a save of the whole settings page.
        """
        now = self.current()
        try:
            write_config(
                {
                    "mode": "solo" if _is_local(now["url"]) else "join",
                    "url": now["url"],
                    "key": now["key"],
                    "display": int(display),
                    "seconds": now["seconds"],
                    "hotkey": now["hotkey"],
                }
            )
        except (OSError, ValueError) as exc:
            warn("set source", exc, "the source was not changed")
            return {"ok": False, "message": str(exc)}

        if self._restart_capture is not None:
            self._restart_capture()
        return {"ok": True, "display": int(display)}

    def install_info(self) -> dict:
        """What this install is, for a screen that is not asking to change it.

        The invite code is the reason this exists: it was printed at startup,
        and the packaged app is built without a console, so the one string
        needed to add a second machine was written where nobody could read it.
        """
        from paths import data_dir
        from server.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        now = self.current()
        hosting = _is_local(now["url"])

        # Whatever Windows is playing to right now, which is what gets
        # recorded. Reported here because the alternative way to find out is to
        # record something worth keeping and play it back.
        try:
            import pyaudiowpatch as pyaudio

            with pyaudio.PyAudio() as sound:
                audio = str(sound.get_default_wasapi_loopback()["name"])
        except Exception as exc:  # noqa: BLE001
            warn("audio device", exc, "settings cannot say whether clips have sound")
            audio = ""

        return {
            "hosting": hosting,
            "server": now["url"],
            "public": settings.public_base_url,
            "invite": settings.invite_code if hosting else "",
            "folder": str(data_dir()),
            "audio": audio,
        }

    def test(self, url: str, key: str) -> dict:
        from capture.probe import check_api

        return check_api(url, key)

    def join(self, url: str, invite: str, handle: str) -> dict:
        """Create an account on the host and keep the key it returns.

        This is what makes a new machine need one shared code rather than a key
        someone had to issue by hand and send over.
        """
        import httpx

        base = (url or "").strip().rstrip("/")
        if not base:
            return {"ok": False, "message": "Enter the server address."}
        if not base.startswith(("http://", "https://")):
            base = f"https://{base}"
        if not handle.strip():
            return {"ok": False, "message": "Pick a name for yourself."}

        try:
            response = httpx.post(
                f"{base}/api/register",
                json={"handle": handle.strip(), "invite": invite.strip()},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            return {"ok": False, "message": f"Cannot reach {base}. {type(exc).__name__}."}

        if response.status_code == 403:
            return {"ok": False, "message": "That invite code was not accepted."}
        if response.status_code == 409:
            return {"ok": False, "message": "That name is taken on this server."}
        if response.status_code != 200:
            return {"ok": False, "message": f"Server said {response.status_code}."}

        return {
            "ok": True,
            "message": "Joined.",
            "base_url": base,
            "key": response.json()["apiKey"],
        }

    def save(self, values: dict) -> dict:
        try:
            write_config(values)
        except OSError as exc:
            return {"ok": False, "message": f"Could not write settings: {exc}"}
        self.saved = True
        _later(self._on_saved)
        return {"ok": True}

    def skip(self) -> dict:
        _later(self._on_skipped)
        return {"ok": True}
