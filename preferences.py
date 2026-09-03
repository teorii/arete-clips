"""User preferences.

Separate from the config file on purpose. That one holds what the app needs to
run at all: where clips go, which display, which key. This holds what someone
wants it to feel like, and every value here is something the app used to decide
on their behalf.

JSON rather than more environment variables, because these change from the
settings screen while the app is running, and an env file is a poor place for
something a UI writes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from paths import data_dir
from problems import warn


@dataclass
class Preferences:
    # Sound is the only confirmation you get with a game in the foreground,
    # so it is on unless someone turns it off.
    play_sound: bool = True
    # Tray balloons. Off is reasonable: a fullscreen game covers them anyway.
    show_notifications: bool = True
    # Off by default: the X hides to the tray and recording continues, which
    # is the point of a background recorder. On makes it quit for real.
    quit_on_close: bool = False
    # Put the link on the clipboard as soon as one is generated.
    copy_link_automatically: bool = True
    # Two clicks to delete a clip. Off makes the trash immediate.
    confirm_delete: bool = True
    # Colour the held-storage bar once it passes this, in gigabytes.
    held_warning_gb: float = 2.0

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


def preferences_file() -> Path:
    return data_dir() / "preferences.json"


def load() -> Preferences:
    """Read preferences, falling back to defaults for anything missing.

    A file written by a newer version may carry keys this one does not know,
    and an older one may lack keys entirely. Both are normal, so unknown keys
    are ignored and absent ones take their default rather than failing.
    """
    path = preferences_file()
    if not path.exists():
        return Preferences()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        spoiled = path.with_suffix(".corrupt")
        try:
            path.replace(spoiled)
        except OSError as move_failure:
            warn("preferences", move_failure, "could not set the bad file aside")
        warn("preferences", exc, f"using defaults; the old file is at {spoiled}")
        return Preferences()

    if not isinstance(raw, dict):
        warn("preferences", f"expected an object, found {type(raw).__name__}", "using defaults")
        return Preferences()

    # This preference was once stored the other way round, as close_to_tray.
    # Carried over rather than dropped, or anyone who had turned it off would
    # silently get the opposite behaviour back.
    if "close_to_tray" in raw and "quit_on_close" not in raw:
        raw["quit_on_close"] = not raw["close_to_tray"]

    known = {name: raw[name] for name in Preferences.field_names() if name in raw}
    try:
        return Preferences(**known)
    except TypeError as exc:
        warn("preferences", exc, "using defaults")
        return Preferences()


def save(preferences: Preferences) -> None:
    """Write preferences, replacing the file atomically.

    Written to a temporary file and renamed, so a crash mid-write cannot leave
    a half-written file that the next start refuses to read.
    """
    path = preferences_file()
    temporary = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(asdict(preferences), indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        warn("preferences", exc, "this change will not survive a restart")


def update(values: dict) -> Preferences:
    """Apply a partial change and persist it."""
    current = asdict(load())
    for name in Preferences.field_names():
        if name in values:
            current[name] = values[name]
    merged = Preferences(**current)
    save(merged)
    return merged
