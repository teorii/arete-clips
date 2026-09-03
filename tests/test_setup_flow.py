"""The setup and settings bridge the desktop window talks to."""

from __future__ import annotations

from setup import AppBridge




def test_the_bridge_can_open_settings():
    """The tray menu is not somewhere anyone finds a setting: Windows files new
    tray icons under the overflow chevron, so the window needs a way in too."""
    opened = []
    api = AppBridge(open_settings=lambda: opened.append(True))

    assert api.open_settings() == {"ok": True}
    assert opened == [True]


def test_opening_settings_without_a_shell_says_so():
    api = AppBridge()

    result = api.open_settings()

    assert result["ok"] is False
    assert "not available" in result["message"]
