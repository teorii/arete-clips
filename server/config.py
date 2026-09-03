from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from paths import config_file


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(config_file()), extra="ignore")

    # A file beside the app's other data. Nothing to install and nothing to
    # keep running, which is the point of an install that hosts itself.
    database_url: str = "sqlite:///./recording.db"
    public_base_url: str = "http://localhost:8000"

    local_storage_dir: Path = Path("./clips_local")

    # Lets someone create their own account, so a new machine needs one shared
    # string rather than a key issued by hand. Blank disables registration,
    # which is the right default for a server nobody else should join.
    invite_code: str = ""

    # Signs local dev upload tokens. Irrelevant once STORAGE_BACKEND=r2.
    upload_secret: str = "dev-only-change-me"
    upload_url_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
