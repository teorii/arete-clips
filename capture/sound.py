"""Audible confirmation that a clip was taken.

The tray balloon is the only feedback the app gives, and a fullscreen game
covers it. Pressing the hotkey and perceiving nothing at all is the worst
moment in the product: you cannot tell whether it worked without alt-tabbing,
which is exactly what you did not want to do mid-fight.

The tones are synthesised rather than shipped as files, for the same reason the
icon is drawn: nothing to lose, nothing to bundle, and the pitch is the whole
design.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from paths import data_dir
from problems import warn

_RATE = 44100

# Rising pair for success, falling for failure. Direction is understood without
# being learned, which matters for a sound heard while looking elsewhere.
_SAVED = ((880.0, 0.07), (1318.5, 0.11))
_FAILED = ((440.0, 0.10), (330.0, 0.16))


def _write_tones(path: Path, tones: tuple[tuple[float, float], ...]) -> None:
    frames = bytearray()
    for frequency, seconds in tones:
        count = int(_RATE * seconds)
        for index in range(count):
            # Fade each tone in and out, or the edges click.
            envelope = min(1.0, index / 400, (count - index) / 400)
            value = math.sin(2 * math.pi * frequency * index / _RATE)
            frames += struct.pack("<h", int(value * envelope * 12000))

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_RATE)
        handle.writeframes(bytes(frames))


def _ensure(name: str, tones: tuple[tuple[float, float], ...]) -> Path | None:
    path = data_dir() / name
    if path.exists():
        return path
    try:
        _write_tones(path, tones)
    except OSError as exc:
        warn("sound", exc, "clips will be silent to take, so watch the tray instead")
        return None
    return path


def _play(path: Path | None) -> None:
    if path is None:
        return
    try:
        import winsound

        # Async so a clip never waits on audio, and never blocks the hotkey.
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as exc:  # noqa: BLE001
        # A missing audio device should not stop a recording, but it does mean
        # the only immediate confirmation of a clip is gone.
        warn("sound", exc, "no audible confirmation when a clip is taken")


def _wanted() -> bool:
    # Read at use rather than cached, so turning it off in settings takes
    # effect on the next clip instead of the next launch.
    from preferences import load

    return load().play_sound


def clip_saved() -> None:
    if _wanted():
        _play(_ensure("saved.wav", _SAVED))


def clip_failed() -> None:
    if _wanted():
        _play(_ensure("failed.wav", _FAILED))
