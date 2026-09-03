"""Schema for the clip service.

Deliberate choices, carried over from the design:
  * Two timestamps. `captured_at` comes from the player's machine and may lag by
    days or simply be wrong; `uploaded_at` is our clock and is the trustworthy
    one. We sort by captured_at because that is what a user remembers.
  * Renditions live in their own table because they are generated lazily. Most
    clips are never watched, so most clips never need a second encode.
  * view_count is denormalized. Views arrive as events and update the counter in
    batches; a viral clip must never become a write storm on this table.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .ids import uuid7


class Base(DeclarativeBase):
    pass


class ClipStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    uploaded = "uploaded"
    ready = "ready"
    failed = "failed"


class Visibility(str, enum.Enum):
    private = "private"
    unlisted = "unlisted"
    public = "public"


class TriggerType(str, enum.Enum):
    hotkey = "hotkey"
    auto = "auto"
    manual = "manual"


class RenditionStatus(str, enum.Enum):
    absent = "absent"
    queued = "queued"
    ready = "ready"
    failed = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[Uuid] = mapped_column(Uuid, primary_key=True, default=uuid7)
    public_slug: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    owner_id: Mapped[Uuid] = mapped_column(Uuid, nullable=False)

    title: Mapped[str | None] = mapped_column(String(200))
    game_id: Mapped[int | None] = mapped_column(Integer)  # null = unrecognized game

    status: Mapped[ClipStatus] = mapped_column(
        Enum(ClipStatus, native_enum=False), default=ClipStatus.pending_upload
    )
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False), default=Visibility.unlisted
    )
    trigger_type: Mapped[TriggerType] = mapped_column(
        Enum(TriggerType, native_enum=False), default=TriggerType.hotkey
    )

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_bytes: Mapped[int | None] = mapped_column(BigInteger)
    storage_key: Mapped[str | None] = mapped_column(String(400))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # Client clock. Untrusted: clamp against uploaded_at before display.
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Server clock. The one to trust.
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    # encoder, gpu, measured fps impact, dropped frames, resolution
    capture_meta: Mapped[dict | None] = mapped_column(JSON)

    # Pinned by the owner. Lives in the database rather than in browser
    # storage so it survives a reinstall and a different machine.
    favorite: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false()
    )

    view_count: Mapped[int] = mapped_column(BigInteger, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    renditions: Mapped[list["ClipRendition"]] = relationship(
        back_populates="clip", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # The dominant authenticated read: one user's library, newest first.
        # Keyset paginated, never OFFSET.
        Index("ix_clips_owner_captured", "owner_id", "captured_at", "id"),
    )


class ClipRendition(Base):
    __tablename__ = "clip_renditions"

    clip_id: Mapped[Uuid] = mapped_column(
        Uuid, ForeignKey("clips.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str] = mapped_column(String(20), primary_key=True)  # source, 720p, thumb

    status: Mapped[RenditionStatus] = mapped_column(
        Enum(RenditionStatus, native_enum=False), default=RenditionStatus.absent
    )
    # Store the key, not a URL. Domains and signing schemes change.
    storage_key: Mapped[str | None] = mapped_column(String(400))
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    clip: Mapped[Clip] = relationship(back_populates="renditions")
