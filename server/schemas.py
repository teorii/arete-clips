from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UploadTargetOut(BaseModel):
    url: str
    method: str
    headers: dict[str, str]
    storage_key: str = Field(serialization_alias="storageKey")


class ClipCreate(BaseModel):
    duration_ms: int = Field(alias="durationMs", gt=0, le=600_000)
    captured_at: datetime = Field(alias="capturedAt")
    source_bytes: int | None = Field(default=None, alias="sourceBytes", ge=0)
    game_id: int | None = Field(default=None, alias="gameId")
    title: str | None = Field(default=None, max_length=200)
    trigger_type: str = Field(default="hotkey", alias="triggerType")
    capture_meta: dict | None = Field(default=None, alias="captureMeta")
    content_hash: str | None = Field(default=None, alias="contentHash", max_length=64)
    has_thumbnail: bool = Field(default=False, alias="hasThumbnail")

    model_config = {"populate_by_name": True}


class ClipCreateOut(BaseModel):
    clip_id: UUID = Field(serialization_alias="clipId")
    public_slug: str = Field(serialization_alias="publicSlug")
    share_url: str = Field(serialization_alias="shareUrl")
    uploads: dict[str, UploadTargetOut]

    model_config = {"populate_by_name": True}


class ClipComplete(BaseModel):
    labels: list[str] = Field(default_factory=lambda: ["source"])


class RenditionOut(BaseModel):
    label: str
    status: str
    bytes: int | None
    url: str | None


class ClipOut(BaseModel):
    clip_id: UUID = Field(serialization_alias="clipId")
    public_slug: str = Field(serialization_alias="publicSlug")
    share_url: str = Field(serialization_alias="shareUrl")
    title: str | None
    status: str
    duration_ms: int = Field(serialization_alias="durationMs")
    captured_at: datetime = Field(serialization_alias="capturedAt")
    uploaded_at: datetime | None = Field(serialization_alias="uploadedAt")
    view_count: int = Field(serialization_alias="viewCount")
    favorite: bool
    capture_meta: dict | None = Field(serialization_alias="captureMeta")
    renditions: list[RenditionOut]

    model_config = {"populate_by_name": True}


class ClipPatch(BaseModel):
    """Every field optional. Only keys actually sent are applied, so a
    rename never silently clears the visibility."""

    title: str | None = Field(default=None, max_length=200)
    visibility: str | None = None
    favorite: bool | None = None


class ClipPage(BaseModel):
    items: list[ClipOut]
    next_cursor: str | None = Field(serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}
