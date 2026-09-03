"""Global hotkey via the Windows RegisterHotKey API.

Deliberately RegisterHotKey rather than a low-level keyboard hook. A
WH_KEYBOARD_LL hook sees every keystroke on the machine, which is behaviourally
indistinguishable from a keylogger and is exactly the kind of thing a
kernel-level anti-cheat is built to notice. RegisterHotKey asks the OS to
deliver one specific chord and nothing else.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

_user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

_HOTKEY_ID = 1

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int


def pump(vk: int, on_press: Callable[[], None], modifiers: int = 0) -> None:
    """Register the hotkey and run the message loop until interrupted."""
    if not _user32.RegisterHotKey(None, _HOTKEY_ID, modifiers | MOD_NOREPEAT, vk):
        raise RuntimeError(
            f"could not register hotkey (vk={vk:#04x}). "
            "Another application probably already owns it."
        )
    try:
        msg = wintypes.MSG()
        while True:
            result = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                return
            if msg.message == WM_HOTKEY:
                on_press()
    finally:
        _user32.UnregisterHotKey(None, _HOTKEY_ID)
