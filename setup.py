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
from collections.abc import Callable
from pathlib import Path

from paths import bundle_dir, config_file


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
    except OSError:
        return None
    return f"data:image/jpeg;base64,{encoded}"


def setup_page() -> str:
    return str(bundle_dir() / "ui" / "setup.html")


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
        "ARETE_API_KEY": "" if hosting else str(values["key"]).strip(),
        "DDAGRAB_OUTPUT_IDX": str(int(values["display"])),
        "CLIP_SECONDS": str(seconds),
        # Enough headroom that a clip is never cut short by the ring wrapping.
        "BUFFER_SECONDS": str(max(60, seconds * 2)),
        "HOTKEY_VK": str(values.get("hotkey", "0x78")),
        # What tells the app not to ask again. Hosting installs have no key at
        # this point, so a key cannot be the signal.
        "SETUP_COMPLETE": "1",
    }

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


class AppBridge:
    """Everything the pages can call, exposed as `pywebview.api`.

    Attached to the one window the app owns, so both the setup page and the
    clip library reach it. The library needs it because held clips live on this
    machine and the server has never heard of them: a Generate link button has
    to reach the client, not the API.
    """

    def __init__(self, uploader_factory=None):
        self._uploader_factory = uploader_factory

    def _uploader(self):
        if self._uploader_factory is None:
            raise RuntimeError("no uploader is available yet")
        return self._uploader_factory()

    def held_clips(self) -> list[dict]:
        """Clips captured on this machine that have no link yet."""
        try:
            entries = self._uploader().waiting()
        except Exception:  # noqa: BLE001
            return []
        return [
            {
                "path": entry["mp4_path"],
                "capturedAt": entry.get("captured_at"),
                "durationMs": entry.get("duration_ms", 0),
                "bytes": entry.get("source_bytes", 0),
                "title": entry.get("title"),
                "thumb": _thumbnail_data_uri(entry.get("thumb_path")),
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
    ):
        super().__init__(uploader_factory)
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
        self._on_saved()
        return {"ok": True}

    def skip(self) -> dict:
        self._on_skipped()
        return {"ok": True}
