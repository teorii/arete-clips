"""Finding a desktop-audio capture device.

Windows ships no loopback capture device. ffmpeg's dshow input can see every
microphone on the machine and nothing that the speakers are actually playing,
which for a gameplay clip is exactly backwards: the ability sounds, pings and
enemy callouts are the part worth keeping.

A virtual audio device fills that gap. This finds it rather than hardcoding a
name, because what provides it decides what it is called, and a machine without
one has to keep working.
"""

from __future__ import annotations

import re
import subprocess

from .ffmpeg import FFMPEG

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Ordered by preference. Every one of these is a "what you hear" device:
# virtual-audio-capturer comes from screen-capture-recorder, the rest are names
# various sound card drivers give their built-in loopback.
_LOOPBACK_HINTS = (
    "virtual-audio-capturer",
    "stereo mix",
    "what u hear",
    "wave out mix",
    "loopback",
)

_DEVICE_LINE = re.compile(r'"([^"]+)"\s*\((audio|video)\)')


def parse_devices(output: str, kind: str = "audio") -> list[str]:
    """Pull device names out of `ffmpeg -list_devices` output.

    The listing goes to stderr and looks like:

        [dshow @ 000001] "Microphone (AT2020USB+)" (audio)
        [dshow @ 000001]   Alternative name "@device_cm_{33D9...}"

    Only the quoted friendly name on a line tagged with the kind counts; the
    alternative names on the following line are not usable as-is.
    """
    names: list[str] = []
    for line in output.splitlines():
        match = _DEVICE_LINE.search(line)
        if match and match.group(2) == kind and match.group(1) not in names:
            names.append(match.group(1))
    return names


def list_audio_devices() -> list[str]:
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    # ffmpeg always exits non-zero for a device listing, since "dummy" is not a
    # real input. The listing itself is on stderr regardless.
    return parse_devices(result.stderr, "audio")


def pick_loopback(devices: list[str]) -> str | None:
    lowered = [(name, name.lower()) for name in devices]
    for hint in _LOOPBACK_HINTS:
        for name, low in lowered:
            if hint in low:
                return name
    return None


def find_loopback_device(preference: str = "auto") -> str | None:
    """Resolve the configured audio device.

    "auto" searches for a loopback device, "none" disables audio, anything else
    is taken as an exact dshow device name.
    """
    choice = (preference or "auto").strip()
    if choice.lower() == "none":
        return None
    if choice.lower() != "auto":
        return choice
    return pick_loopback(list_audio_devices())
