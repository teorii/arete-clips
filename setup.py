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

import re
from collections.abc import Callable
from pathlib import Path

from paths import bundle_dir, config_file


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
    updates = {
        "API_BASE_URL": str(values["url"]).rstrip("/"),
        "ARETE_API_KEY": str(values["key"]).strip(),
        "DDAGRAB_OUTPUT_IDX": str(int(values["display"])),
        "CLIP_SECONDS": str(seconds),
        # Enough headroom that a clip is never cut short by the ring wrapping.
        "BUFFER_SECONDS": str(max(60, seconds * 2)),
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


class SetupApi:
    """Bridge exposed to the setup page as `pywebview.api`."""

    def __init__(self, on_saved: Callable[[], None], on_skipped: Callable[[], None]):
        self._on_saved = on_saved
        self._on_skipped = on_skipped
        self.saved = False

    def probe(self) -> dict:
        from capture.probe import gpu_name, has_nvenc, list_displays

        displays = list_displays()
        return {
            "encoder_ok": has_nvenc(),
            "gpu": gpu_name(),
            "displays": displays,
        }

    def test(self, url: str, key: str) -> dict:
        from capture.probe import check_api

        return check_api(url, key)

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
