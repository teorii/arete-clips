"""Where an install keeps its data.

Both of these defaults were relative once, so the database and the clip files
landed in whatever directory the app was started from. A shortcut, a terminal
and Explorer each gave a different one, which read as clips vanishing between
launches, and none of it sat in the folder holding the rest of the install.
"""

from __future__ import annotations

from pathlib import Path

from paths import data_dir
from server.config import Settings


def _default(name: str):
    return Settings.model_fields[name].default


def test_the_database_default_is_absolute_and_beside_the_other_data():
    default = _default("database_url")
    assert default.startswith("sqlite:///")

    path = Path(default.removeprefix("sqlite:///"))
    assert path.is_absolute(), f"{default} is resolved against the working directory"
    assert path.parent == data_dir()


def test_the_clip_store_default_is_absolute_and_beside_the_other_data():
    path = _default("local_storage_dir")
    assert path.is_absolute(), f"{path} is resolved against the working directory"
    assert path.parent == data_dir()
