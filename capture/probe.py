"""Checks the setup screen runs before the app trusts a machine.

Everything here answers a question the user would otherwise discover by
pressing the hotkey and getting nothing: is there a hardware encoder, which
display is the game on, and does the server accept this key.
"""

from __future__ import annotations

import re
import subprocess

import httpx

from .ffmpeg import FFMPEG

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_STREAM = re.compile(r"Video:.*?,\s*(\d{3,5})x(\d{3,5})")


def _run(args: list[str], timeout: float = 25.0) -> subprocess.CompletedProcess:
    # stdin must be given explicitly. Packaged with no console there is no
    # valid standard input to inherit, and a child that tries to use it hangs
    # rather than failing, which looks like the app having frozen.
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )


def has_nvenc() -> bool:
    """Whether this ffmpeg can actually open the NVIDIA encoder.

    Listing encoders is not enough: ffmpeg lists h264_nvenc on machines with no
    NVIDIA card at all, and fails only when something tries to use it. So this
    encodes a single frame.
    """
    try:
        result = _run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=256x144:r=30",
            "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
        ])
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def gpu_name() -> str:
    try:
        result = _run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=8
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def list_displays(maximum: int = 4) -> list[dict]:
    """Capture one frame from each desktop output to see what is there."""
    displays: list[dict] = []
    for index in range(maximum):
        try:
            result = _run([
                FFMPEG, "-hide_banner", "-loglevel", "info",
                "-f", "lavfi", "-i", f"ddagrab=output_idx={index}:framerate=30",
                "-frames:v", "1", "-f", "null", "-",
            ], timeout=20)
        except (OSError, subprocess.SubprocessError):
            break
        if result.returncode != 0:
            # The first unavailable index means there are no more displays.
            break
        match = _STREAM.search(result.stderr)
        displays.append(
            {
                "index": index,
                "width": int(match.group(1)) if match else 0,
                "height": int(match.group(2)) if match else 0,
            }
        )
    return displays


def check_api(base_url: str, api_key: str, timeout: float = 10.0) -> dict:
    """Does the server answer, and does it accept this key."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "message": "Enter the server address."}
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"

    try:
        health = httpx.get(f"{base}/healthz", timeout=timeout)
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Cannot reach {base}. {type(exc).__name__}."}
    if health.status_code != 200:
        return {"ok": False, "message": f"{base} answered {health.status_code}, not a server."}

    if not api_key.strip():
        return {"ok": False, "message": "Server is reachable. Now enter your key."}

    try:
        library = httpx.get(
            f"{base}/api/clips?limit=1",
            headers={"X-API-Key": api_key.strip()},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Server stopped responding. {type(exc).__name__}."}

    if library.status_code == 401:
        return {"ok": False, "message": "Server is reachable but rejected that key."}
    if library.status_code != 200:
        return {"ok": False, "message": f"Unexpected reply: {library.status_code}."}
    return {"ok": True, "message": "Connected.", "base_url": base}
