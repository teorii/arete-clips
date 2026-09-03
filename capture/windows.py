"""Which display is a given program on.

Capture works per display, not per window: ddagrab is Desktop Duplication, and
the alternatives that grab a single window either cost CPU or look like the
kind of injection an anti-cheat objects to.

So picking "the game" means finding the window, working out which monitor it is
on, and capturing that. This lists what is running with a visible window and
the display index each one would be captured from, so the choice can be made by
program name rather than by remembering that League is on monitor 2.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_MONITOR_DEFAULTTONEAREST = 2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


_ENUM_MONITORS = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_double
)
_ENUM_WINDOWS = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def monitors() -> list[dict]:
    """Displays in enumeration order, which is the order ddagrab indexes them."""
    found: list[dict] = []

    def collect(handle, _dc, _rect, _data):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if _user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            box = info.rcMonitor
            found.append({
                "handle": handle,
                "left": box.left,
                "top": box.top,
                "width": box.right - box.left,
                "height": box.bottom - box.top,
                "primary": bool(info.dwFlags & 1),
            })
        return 1

    _user32.EnumDisplayMonitors(None, None, _ENUM_MONITORS(collect), 0)
    # Deliberately unsorted. Sorting these by position looks tidier and is
    # wrong: checked against ddagrab on a three-monitor setup, the native
    # enumeration order is what matches its output_idx, while left-to-right
    # order does not. Reordering here silently captures the wrong screen.
    return found


def _process_name(pid: int) -> str:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return buffer.value.rsplit("\\", 1)[-1]
    finally:
        _kernel32.CloseHandle(handle)
    return ""


# Programs that own a window but are never what someone is recording.
_NOT_A_GAME = {
    "explorer.exe", "applicationframehost.exe", "textinputhost.exe",
    "searchhost.exe", "shellexperiencehost.exe", "systemsettings.exe",
    "arete.exe", "python.exe", "pythonw.exe", "rtkuwp.exe",
}

# Names that read better than the executable does.
_FRIENDLY = {
    "league of legends.exe": "League of Legends",
    "valorant-win64-shipping.exe": "VALORANT",
    "cs2.exe": "Counter-Strike 2",
    "overwatch.exe": "Overwatch",
    "rocketleague.exe": "Rocket League",
    "fortniteclient-win64-shipping.exe": "Fortnite",
    "dota2.exe": "Dota 2",
    "minecraft.windows.exe": "Minecraft",
}


def friendly_name(process: str) -> str:
    """A name worth putting on a clip."""
    known = _FRIENDLY.get(process.lower())
    if known:
        return known
    stem = process.rsplit(".", 1)[0]
    # Executables are rarely capitalised the way the game is written.
    return stem.replace("-", " ").replace("_", " ").strip() or process


def foreground_program() -> dict | None:
    """Whatever the user was looking at."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = _process_name(pid.value)
    if not name:
        return None
    handle = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    screens = monitors()
    index = next(
        (i for i, m in enumerate(screens) if m["handle"] == handle), 0
    )
    return {"process": name, "display_index": index, "name": friendly_name(name)}


def program_on_display(display_index: int) -> str | None:
    """What is being recorded on a given display, as a name for the clip.

    The foreground window first, since that is what someone was looking at when
    they hit the key. If they alt-tabbed to something on another screen, fall
    back to whatever else is on the display actually being captured, so the
    clip is not named after the browser they happened to click.
    """
    front = foreground_program()
    if (
        front
        and front["display_index"] == display_index
        and front["process"].lower() not in _NOT_A_GAME
    ):
        return front["name"]

    for window in visible_windows():
        if (
            window["display_index"] == display_index
            and window["process"].lower() not in _NOT_A_GAME
        ):
            return friendly_name(window["process"])
    return None


def visible_windows() -> list[dict]:
    """Programs with a real window, and the display each sits on."""
    screens = monitors()
    by_handle = {m["handle"]: index for index, m in enumerate(screens)}
    results: list[dict] = []
    seen: set[str] = set()

    def collect(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, title, length + 1)

        box = _RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(box))
        # Skip the zero-sized and off-screen windows Windows keeps around.
        if box.right - box.left < 200 or box.bottom - box.top < 200:
            return True

        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = _process_name(pid.value)
        if not name or name in seen:
            return True
        seen.add(name)

        handle = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        index = by_handle.get(handle, 0)
        screen = screens[index] if index < len(screens) else {}
        results.append({
            "process": name,
            "title": title.value[:60],
            "display_index": index,
            "width": screen.get("width", 0),
            "height": screen.get("height", 0),
        })
        return True

    _user32.EnumWindows(_ENUM_WINDOWS(collect), 0)
    results.sort(key=lambda w: w["process"].lower())
    return results
