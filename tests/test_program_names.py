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
