from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the server boots with no external dependency.
    # Point at Supabase to get the real schema: postgresql+psycopg://...
    database_url: str = "sqlite:///./recording.db"
    public_base_url: str = "http://localhost:8000"

    # "local" writes to disk through a signed-URL contract that mirrors S3.
    # "r2" issues real presigned PUTs against Cloudflare R2.
    storage_backend: str = "local"
    local_storage_dir: Path = Path("./clips_local")

    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "clips"
    r2_public_base_url: str = ""

    # Signs local dev upload tokens. Irrelevant once STORAGE_BACKEND=r2.
    upload_secret: str = "dev-only-change-me"
    upload_url_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
