"""One copy of the app at a time.

The tray icon lives under the Windows overflow chevron, so a running Arete is
easy to miss and easy to launch again. The second copy used to die on whichever
resource it reached first: the ring buffer segment ffmpeg still held open, or
port 8000. Both surfaced as a traceback, which reads like the app is broken
rather than already open.

A named mutex is the check. Windows keeps it for the lifetime of the process
and releases it on exit however the process ends, including a kill, so there is
no stale lock to clear the way a lock file would leave one after a crash.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from branding import APP_ID, APP_NAME

ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

# Held for the process lifetime: releasing the handle releases the mutex, so
# this deliberately outlives the function that creates it.
_handle: int | None = None


_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _app_window() -> int | None:
    """The running copy's window, whichever screen it is showing.

    Found by scanning rather than by exact title: the window is "Arete" once
    configured but "Arete setup" during first run, and first run is exactly
    when someone is most likely to launch a second copy.
    """
    match: list[int] = []

    def visit(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == APP_NAME or buf.value.startswith(f"{APP_NAME} "):
            match.append(hwnd)
            return False
        return True

    _user32.EnumWindows(_ENUM_PROC(visit), 0)
    return match[0] if match else None


def _show_existing_window() -> bool:
    """Raise the window the running copy already has, if it can be found."""
    hwnd = _app_window()
    if hwnd is None:
        return False
    SW_RESTORE = 9
    _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
    return True


def claim(name: str = APP_ID) -> bool:
    """True if this process is the only copy, False if one is already running.

    On False the running copy's window is brought forward, which is what
    double-clicking the icon was asking for.

    The name is an argument so a test can claim something of its own: sharing
    the app's name would mean the suite failed whenever Arete happened to be
    running, which is most of the time on the machine developing it.
    """
    global _handle

    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    # "Local" is the session namespace: one copy per logged-in user, which
    # is the right scope for a per-user recorder.
    scoped = rf"Local\{name}"
    _handle = _kernel32.CreateMutexW(None, False, scoped)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _show_existing_window()
        return False
    return True
