"""Naming a clip after what was on screen.

A library of timestamps tells you nothing, so the clip carries the program it
came from. Only the naming is tested here: resolving a window to a display
needs a real desktop.
"""

from capture.windows import _NOT_A_GAME, friendly_name


def test_known_games_get_their_real_name():
    assert friendly_name("League of Legends.exe") == "League of Legends"
    assert friendly_name("cs2.exe") == "Counter-Strike 2"


def test_matching_ignores_case():
    assert friendly_name("LEAGUE OF LEGENDS.EXE") == "League of Legends"


def test_unknown_programs_are_tidied_rather_than_dropped():
    """An unrecognised game should still read as a name, not a filename."""
    assert friendly_name("MyGame-Win64-Shipping.exe") == "MyGame Win64 Shipping"
    assert friendly_name("some_game.exe") == "some game"


def test_a_name_is_always_produced():
    for awkward in ("game.exe", "game", "a.b.exe"):
        assert friendly_name(awkward)


def test_the_app_never_names_a_clip_after_itself():
    for name in ("arete.exe", "explorer.exe", "python.exe"):
        assert name in _NOT_A_GAME


def test_a_hosting_install_counts_as_configured(tmp_path, monkeypatch):
    """Regression: solo setup writes no key, because the app creates the
    account on first start. Requiring a key sent those installs back to setup
    on every launch."""
    import paths

    config = tmp_path / "config.env"
    monkeypatch.setattr(paths, "config_file", lambda: config)

    config.write_text("API_BASE_URL=http://localhost:8000\nARETE_API_KEY=\n", encoding="utf-8")
    assert paths.is_configured() is False, "no marker should mean not set up"

    config.write_text(
        "API_BASE_URL=http://localhost:8000\nARETE_API_KEY=\nSETUP_COMPLETE=1\n",
        encoding="utf-8",
    )
    assert paths.is_configured() is True, "a hosting install must count as configured"


def test_a_clip_is_named_for_the_screen_and_the_time(monkeypatch):
    """Naming a clip after the program in front sounded better and read worse:
    the answer was whatever held focus when the hotkey arrived, which on a
    second monitor is rarely the game."""
    from datetime import datetime, timezone

    from capture.daemon import Daemon

    monkeypatch.setattr("capture.windows.monitors",
                        lambda: [{"number": 2}, {"number": 3}, {"number": 1}])

    daemon = Daemon.__new__(Daemon)
    daemon.s = type("S", (), {"ddagrab_output_idx": 1})()

    when = datetime(2026, 9, 3, 21, 5, tzinfo=timezone.utc)
    name = daemon.clip_name(when)

    # Numbered as Windows numbers it, so it matches the source shown in the app.
    assert name.startswith("Display 3 at ")
    assert len(name.split(" at ")[1]) == 5


def test_a_display_windows_cannot_name_falls_back_to_capture_order(monkeypatch):
    from datetime import datetime, timezone

    from capture.daemon import Daemon

    monkeypatch.setattr("capture.windows.monitors", lambda: [])

    daemon = Daemon.__new__(Daemon)
    daemon.s = type("S", (), {"ddagrab_output_idx": 0})()

    assert daemon.clip_name(datetime.now(timezone.utc)).startswith("Display 1 at ")
