from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from paths import config_file


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(config_file()), extra="ignore")

    # SQLite by default so the server boots with no external dependency.
    # Point at Supabase to get the real schema: postgresql+psycopg://...
    database_url: str = "sqlite:///./recording.db"
    public_base_url: str = "http://localhost:8000"

    # "local" writes to disk through a signed-URL contract that mirrors S3.
    # "s3" issues real presigned PUTs against anything speaking the S3 API:
    # MinIO on this machine, MinIO on a server, Cloudflare R2, or S3 itself.
    storage_backend: str = "local"
    local_storage_dir: Path = Path("./clips_local")

    # http://127.0.0.1:9000 for a local MinIO,
    # https://<account>.r2.cloudflarestorage.com for R2.
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket: str = "clips"
    # R2 wants "auto"; MinIO ignores it.
    s3_region: str = "auto"
    # Where a browser fetches clips from. Usually the endpoint for MinIO, or a
    # public bucket domain for R2.
    s3_public_base_url: str = ""
    # Where clients are told to upload to. Distinct from s3_endpoint_url, which
    # is how this process reaches storage: on a self-hosted setup that is
    # localhost, and handing localhost to a client on another machine gives it
    # an upload URL pointing at itself. Blank means they are the same.
    s3_public_endpoint_url: str = ""
    # MinIO addresses buckets by path, not subdomain. R2 accepts either, so
    # path style is the setting that works everywhere.
    s3_force_path_style: bool = True

    # Signs local dev upload tokens. Irrelevant once STORAGE_BACKEND=r2.
    upload_secret: str = "dev-only-change-me"
    upload_url_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
