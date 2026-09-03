from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from paths import config_file, data_dir


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(config_file()), extra="ignore")

    # A file beside the app's other data. Nothing to install and nothing to
    # keep running, which is the point of an install that hosts itself.
    #
    # Absolute, and deliberately. These were relative once, which meant the
    # database landed in whatever directory the app happened to be started
    # from: a different one per shortcut, so clips saved yesterday were simply
    # missing today, and none of it in the folder that holds the rest of the
    # install. data_dir() is the project directory when running from source, so
    # development keeps the same file it always had.
    database_url: str = f"sqlite:///{(data_dir() / 'recording.db').as_posix()}"
    public_base_url: str = "http://localhost:8000"

    local_storage_dir: Path = data_dir() / "clips_local"

    # Lets someone create their own account, so a new machine needs one shared
    # string rather than a key issued by hand. Blank disables registration,
    # which is the right default for a server nobody else should join.
    invite_code: str = ""

    # Signs the upload tokens that let a client PUT bytes straight to storage.
    upload_secret: str = "dev-only-change-me"
    upload_url_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
