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


def test_settings_is_its_own_page_not_the_setup_one():
    """Settings used to load the setup screen, which offered to skip and to
    re-choose where clips live, neither of which means anything once a machine
    is running."""
    from pathlib import Path

    from setup import settings_page, setup_page

    assert settings_page() != setup_page()
    assert Path(settings_page()).exists()

    markup = Path(settings_page()).read_text(encoding="utf-8")
    assert "Skip for now" not in markup
    assert "Save changes" in markup and "Cancel" in markup
    for control in ("hotkey", "seconds", "bitrate", "display"):
        assert f'id="{control}"' in markup, f"settings cannot change {control}"


def test_closing_settings_is_not_skipping_setup():
    closed = []
    api = AppBridge(close_settings=lambda: closed.append(True))

    assert api.close_settings() == {"ok": True}
    assert closed == [True]


def test_quality_is_only_written_when_the_page_offers_it(tmp_path, monkeypatch):
    """Setup never asks about quality, so its save must not write a default
    over whatever the config already says."""
    import setup as setup_module

    config = tmp_path / "config.env"
    config.write_text("CAPTURE_BITRATE=20M\n", encoding="utf-8")
    monkeypatch.setattr(setup_module, "config_file", lambda: config)

    base = {"mode": "solo", "url": "", "key": "", "display": 0,
            "seconds": 30, "hotkey": "0x78"}

    setup_module.write_config(base)
    assert "CAPTURE_BITRATE=20M" in config.read_text(encoding="utf-8")

    setup_module.write_config({**base, "bitrate": "6M"})
    assert "CAPTURE_BITRATE=6M" in config.read_text(encoding="utf-8")
