"""The setup and settings bridge the desktop window talks to."""

from __future__ import annotations

from setup import AppBridge




def test_the_bridge_can_open_settings():
    """The tray menu is not somewhere anyone finds a setting: Windows files new
    tray icons under the overflow chevron, so the window needs a way in too."""
    import threading as _threading

    opened = _threading.Event()
    api = AppBridge(open_settings=opened.set)

    assert api.open_settings() == {"ok": True}
    # Deferred, so the call can answer before the window navigates away from
    # the page waiting on it.
    assert opened.wait(timeout=3)


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

    # Capture selects a display, so that is the only thing the page offers.
    # Programs were offered alongside them once, which implied a single window
    # could be recorded and made several options mean the same display.
    assert "info.displays" in markup
    assert "programs" not in markup, "a program is not something capture can select"


def test_closing_settings_is_not_skipping_setup():
    import threading as _threading

    closed = _threading.Event()
    api = AppBridge(close_settings=closed.set)

    assert api.close_settings() == {"ok": True}
    assert closed.wait(timeout=3)


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


def test_sources_are_labelled_for_the_pages_that_show_them(monkeypatch):
    """The settings page rendered an empty dropdown because it invented its own
    label from a field the probe does not return. One name, built here."""
    import setup as setup_module

    monkeypatch.setattr(
        "capture.probe.list_displays",
        lambda: [{"index": 0, "width": 1920, "height": 1080},
                 {"index": 1, "width": 2560, "height": 1440}],
    )
    # ddagrab's index is not Windows' display number, and labelling by index
    # named a different screen than Display Settings does.
    monkeypatch.setattr(
        "capture.windows.monitors",
        lambda: [{"number": 2}, {"number": 3}],
    )

    api = setup_module.SetupApi(on_saved=lambda: None, on_skipped=lambda: None)
    found = api.sources()

    # A number and a resolution. Programs were listed here once, which implied
    # capture could select a window, and churned as windows moved.
    assert "programs" not in found
    assert found["displays"][0]["label"] == "Display 2 (1920x1080)"
    assert found["displays"][1]["label"] == "Display 3 (2560x1440)"
    for entry in found["displays"]:
        assert entry["label"].strip(), "an unlabelled entry renders as a blank row"


def test_changing_the_source_keeps_every_other_setting(tmp_path, monkeypatch):
    """Changing one thing from the library must not rewrite the rest of the
    config as a side effect."""
    import setup as setup_module

    config = tmp_path / "config.env"
    config.write_text(
        "API_BASE_URL=http://localhost:8000\nARETE_API_KEY=\n"
        "DDAGRAB_OUTPUT_IDX=0\nCLIP_SECONDS=90\nHOTKEY_VK=0x7A\n"
        "CAPTURE_BITRATE=20M\nSETUP_COMPLETE=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_module, "config_file", lambda: config)

    restarted = []
    api = setup_module.SetupApi(
        on_saved=lambda: None,
        on_skipped=lambda: None,
        restart_capture=lambda: restarted.append(True),
    )

    assert api.set_source(2) == {"ok": True, "display": 2}
    assert restarted == [True], "capture reads its display at construction"

    written = config.read_text(encoding="utf-8")
    assert "DDAGRAB_OUTPUT_IDX=2" in written
    assert "CLIP_SECONDS=90" in written
    assert "HOTKEY_VK=0x7A" in written
    assert "CAPTURE_BITRATE=20M" in written


def test_navigation_waits_until_the_call_has_answered():
    """pywebview resolves an api call by evaluating JavaScript that looks up a
    callback the page registered. Navigating from inside the call throws that
    registry away, the resolve lands on a page that has never heard of it, and
    the bridge is wedged for every call after it:

        TypeError: window.pywebview._returnValuesCallbacks.save... is not a
        function

    Saving settings did this, which is why generating a link stopped working
    once you had opened the settings screen.
    """
    import threading as _threading

    navigated = _threading.Event()
    api = AppBridge(close_settings=navigated.set)

    result = api.close_settings()

    assert result == {"ok": True}
    assert not navigated.is_set(), "navigated before the call could answer"
    assert navigated.wait(timeout=3), "navigation never happened"


def test_a_failed_navigation_is_reported_not_swallowed(capsys):
    def explode() -> None:
        raise RuntimeError("window is gone")

    api = AppBridge(close_settings=explode)
    api.close_settings()

    import time

    time.sleep(0.5)
    # problems.warn reports on stdout, which is where the app's log picks it up.
    assert "window is gone" in capsys.readouterr().out
