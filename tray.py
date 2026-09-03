"""System tray icon.

A clip tool that stops recording when you close its window is not much use
during a game, so closing hides the window instead of quitting. The tray icon
is how you bring it back, clip without touching the keyboard, and quit for
real.

The icon doubles as a status light: the mark is warm while the ring buffer is
running and grey when capture failed to start and only the library works.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pystray

from branding import APP_NAME, icon_image

# Windows hands the notification area a 16px icon on a 100% display and scales
# from whatever it is given, so render above the largest DPI it will ask for.
_SIZE = 64


class Tray:
    def __init__(
        self,
        on_open: Callable[[], None],
        on_clip: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_open = on_open
        self._on_clip = on_clip
        self._on_quit = on_quit
        self.recording = True

        menu = pystray.Menu(
            # default=True makes this fire on a double-click of the icon.
            pystray.MenuItem(f"Open {APP_NAME}", self._open, default=True),
            pystray.MenuItem("Clip the last 30 seconds", self._clip),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Quit {APP_NAME}", self._quit),
        )
        # The third argument is the hover tooltip, which is the only place the
        # name shows once the window is hidden.
        self.icon = pystray.Icon(
            APP_NAME.lower(), icon_image(_SIZE, recording=True), APP_NAME, menu
        )
        self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)

    # pystray passes the item into callbacks; the app does not care about it.

    def _status_text(self, _item=None) -> str:
        return "Recording" if self.recording else "Capture unavailable"

    def _open(self, _icon=None, _item=None) -> None:
        self._on_open()

    def _clip(self, _icon=None, _item=None) -> None:
        self._on_clip()

    def _quit(self, _icon=None, _item=None) -> None:
        self._on_quit()

    # ---------------------------------------------------------------- control

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass  # already torn down; nothing useful to do during shutdown

    def set_recording(self, recording: bool) -> None:
        self.recording = recording
        try:
            self.icon.icon = icon_image(_SIZE, recording=recording)
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass  # cosmetic only, never worth taking the app down for

    def notify(self, message: str, title: str = APP_NAME) -> None:
        """Balloon notification. Best effort: some Windows configurations
        suppress these entirely, and a missing toast is not worth an error."""
        try:
            self.icon.notify(message, title)
        except Exception:  # noqa: BLE001
            pass
