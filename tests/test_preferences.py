"""Preferences.

Distinct from the config file: that holds what the app needs to run, this holds
what it should feel like. Every value here replaced something the app used to
decide on the user's behalf.
"""

import json

import pytest

import preferences as prefs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real preferences while testing."""
    monkeypatch.setattr(prefs, "preferences_file", lambda: tmp_path / "preferences.json")


def test_defaults_apply_when_there_is_no_file():
    loaded = prefs.load()
    assert loaded.play_sound is True
    assert loaded.quit_on_close is False
    assert loaded.held_warning_gb == 2.0


def test_a_change_survives_a_reload():
    prefs.update({"play_sound": False, "held_warning_gb": 5.0})
    reloaded = prefs.load()
    assert reloaded.play_sound is False
    assert reloaded.held_warning_gb == 5.0
    # Untouched values keep their defaults rather than being reset.
    assert reloaded.quit_on_close is False


def test_unknown_keys_are_ignored(tmp_path):
    """A file from a newer version must not stop an older one from starting."""
    (tmp_path / "preferences.json").write_text(
        json.dumps({"play_sound": False, "invented_later": 42}), encoding="utf-8"
    )
    assert prefs.load().play_sound is False


def test_missing_keys_take_their_default(tmp_path):
    (tmp_path / "preferences.json").write_text(
        json.dumps({"play_sound": False}), encoding="utf-8"
    )
    assert prefs.load().show_notifications is True


def test_a_corrupt_file_is_preserved_and_reported(tmp_path, capsys):
    path = tmp_path / "preferences.json"
    path.write_text("{ not json", encoding="utf-8")

    assert prefs.load().play_sound is True, "should fall back to defaults"
    assert path.with_suffix(".corrupt").exists(), "the bad file was thrown away"
    assert "preferences" in capsys.readouterr().out, "the failure was not reported"


def test_a_file_that_is_not_an_object_is_refused(tmp_path, capsys):
    (tmp_path / "preferences.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert prefs.load().play_sound is True
    assert "preferences" in capsys.readouterr().out


def test_saving_does_not_leave_a_partial_file(tmp_path):
    """Written to a temp file and renamed, so a crash mid-write cannot leave
    something the next start refuses to read."""
    prefs.update({"play_sound": False})
    path = tmp_path / "preferences.json"
    assert json.loads(path.read_text(encoding="utf-8"))["play_sound"] is False
    assert not path.with_suffix(".tmp").exists(), "temp file left behind"


def test_the_old_close_to_tray_setting_is_carried_over(tmp_path):
    """It was stored the other way round. Dropping it would silently hand
    someone who had turned it off the opposite behaviour."""
    (tmp_path / "preferences.json").write_text(
        json.dumps({"close_to_tray": False}), encoding="utf-8"
    )
    assert prefs.load().quit_on_close is True

    (tmp_path / "preferences.json").write_text(
        json.dumps({"close_to_tray": True}), encoding="utf-8"
    )
    assert prefs.load().quit_on_close is False
