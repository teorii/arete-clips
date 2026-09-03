"""Clip API and share pages.

No endpoint here accepts video bytes in production. The client asks for a place
to put them, PUTs directly to object storage, then reports back. This service
only ever moves metadata.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from .auth import require_user
from .config import get_settings
from .db import get_db
from .ids import public_slug, uuid7
from .migrate import ensure_schema
from .models import (
    Clip,
    ClipRendition,
    ClipStatus,
    RenditionStatus,
    TriggerType,
    User,
    Visibility,
)
from .media import MediaError, duration_seconds, poster, trim
from .schemas import (
    ClipComplete,
    ClipCreate,
    ClipCreateOut,
    ClipOut,
    ClipPage,
    ClipPatch,
    ClipTrim,
    RenditionOut,
    UploadTargetOut,
)
from .storage import LocalStorage, get_storage

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_schema()
    yield


app = FastAPI(title="Clip Service", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# The owner of any clip captured before keys existed. Kept as a real user so
# an upgrade does not orphan an existing library.
LEGACY_OWNER_ID = UUID("00000000-0000-7000-8000-000000000001")

_CONTENT_TYPES = {"source": "video/mp4", "thumb": "image/jpeg"}


# The Vite dev server runs on its own origin. In production the built app is
# served from this process, so this only matters while developing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _storage_key(clip_id: UUID, captured: datetime, label: str) -> str:
    ext = "jpg" if label == "thumb" else "mp4"
    return f"clips/{captured:%Y/%m}/{clip_id}/{label}.{ext}"


def _share_url(slug: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/c/{slug}"


def _as_utc(value: datetime | None) -> datetime | None:
    """Stamp UTC onto a naive timestamp before it leaves the API.

    Everything is stored in UTC, but SQLite has no timezone type, so values
    round-trip naive and serialize without an offset. A client parsing an
    offset-less ISO string treats it as local time, which silently shifts every
    capture time by the viewer's UTC offset. Postgres timestamptz does not have
    this problem, which is exactly why it is easy to miss in development.
    """
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _rendition_url(clip: Clip, rendition: ClipRendition) -> str | None:
    """Playback URL, versioned.

    Trimming rewrites a rendition in place, so the bytes change while the key
    stays the same. Without a version in the URL every cache in the chain keeps
    serving the old file and an edit looks like it silently failed.
    """
    if not rendition.storage_key:
        return None
    return f"{get_storage().playback_url(rendition.storage_key)}?v={clip.version}"


def _clip_out(clip: Clip) -> ClipOut:
    storage = get_storage()
    return ClipOut(
        clip_id=clip.id,
        public_slug=clip.public_slug,
        share_url=_share_url(clip.public_slug),
        title=clip.title,
        status=clip.status.value,
        duration_ms=clip.duration_ms,
        captured_at=_as_utc(clip.captured_at),
        uploaded_at=_as_utc(clip.uploaded_at),
        view_count=clip.view_count,
        favorite=clip.favorite,
        capture_meta=clip.capture_meta,
        renditions=[
            RenditionOut(
                label=r.label,
                status=r.status.value,
                bytes=r.bytes,
                url=_rendition_url(clip, r),
            )
            for r in clip.renditions
        ],
    )


# ---------------------------------------------------------------- write path


@app.post("/api/clips", response_model=ClipCreateOut, response_model_by_alias=True)
def create_clip(
    payload: ClipCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> ClipCreateOut:
    """Reserve a clip row and hand back somewhere to put the bytes."""
    clip_id = uuid7()
    now = datetime.now(timezone.utc)

    captured = payload.captured_at
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    # The client clock is untrusted. A clip cannot have been captured in the
    # future, so clamp forward skew against our own clock.
    if captured > now:
        captured = now

    try:
        trigger = TriggerType(payload.trigger_type)
    except ValueError:
        trigger = TriggerType.hotkey

    clip = Clip(
        id=clip_id,
        public_slug=public_slug(),
        owner_id=user.id,
        title=payload.title,
        game_id=payload.game_id,
        status=ClipStatus.pending_upload,
        visibility=Visibility.unlisted,
        trigger_type=trigger,
        duration_ms=payload.duration_ms,
        source_bytes=payload.source_bytes,
        content_hash=payload.content_hash,
        captured_at=captured,
        capture_meta=payload.capture_meta,
        storage_key=_storage_key(clip_id, captured, "source"),
    )
    db.add(clip)

    storage = get_storage()
    labels = ["source"] + (["thumb"] if payload.has_thumbnail else [])
    uploads: dict[str, UploadTargetOut] = {}
    for label in labels:
        key = _storage_key(clip_id, captured, label)
        db.add(
            ClipRendition(
                clip_id=clip_id,
                label=label,
                status=RenditionStatus.queued,
                storage_key=key,
            )
        )
        target = storage.create_upload(key, _CONTENT_TYPES[label])
        uploads[label] = UploadTargetOut(
            url=target.url,
            method=target.method,
            headers=target.headers,
            storage_key=key,
        )

    db.commit()
    return ClipCreateOut(
        clip_id=clip_id,
        public_slug=clip.public_slug,
        share_url=_share_url(clip.public_slug),
        uploads=uploads,
    )


@app.post(
    "/api/clips/{clip_id}/complete",
    response_model=ClipOut,
    response_model_by_alias=True,
)
def complete_clip(
    clip_id: UUID,
    payload: ClipComplete,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> ClipOut:
    """Confirm the bytes landed. Verified against storage, not trusted."""
    clip = db.get(Clip, clip_id)
    if clip is None or clip.deleted_at is not None or clip.owner_id != user.id:
        # Not 403: telling someone their key is valid but the clip is not
        # theirs confirms the clip exists.
        raise HTTPException(404, "clip not found")

    storage = get_storage()
    missing: list[str] = []
    for label in payload.labels:
        rendition = db.get(ClipRendition, (clip_id, label))
        if rendition is None or not rendition.storage_key:
            missing.append(label)
            continue
        size = storage.size_of(rendition.storage_key)
        if not size:
            rendition.status = RenditionStatus.failed
            missing.append(label)
            continue
        rendition.status = RenditionStatus.ready
        rendition.bytes = size

    if "source" in missing:
        clip.status = ClipStatus.failed
        db.commit()
        raise HTTPException(409, f"upload not found in storage for: {missing}")

    clip.status = ClipStatus.ready
    clip.uploaded_at = datetime.now(timezone.utc)
    source = db.get(ClipRendition, (clip_id, "source"))
    if source is not None:
        clip.source_bytes = source.bytes
    db.commit()
    db.refresh(clip)
    return _clip_out(clip)


# ----------------------------------------------------------------- read path


@app.get("/api/clips", response_model=ClipPage, response_model_by_alias=True)
def list_clips(
    cursor: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    favorite: bool | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> ClipPage:
    """Keyset pagination, never OFFSET.

    OFFSET degrades linearly with depth, which punishes exactly the heavy users
    with thousands of clips that you least want to annoy.
    """
    stmt = (
        select(Clip)
        .where(Clip.owner_id == user.id, Clip.deleted_at.is_(None))
        .order_by(Clip.captured_at.desc(), Clip.id.desc())
        .limit(limit + 1)
    )
    if q:
        needle = f"%{q}%"
        stmt = stmt.where(or_(Clip.title.ilike(needle), Clip.public_slug.ilike(needle)))
    if favorite is not None:
        stmt = stmt.where(Clip.favorite.is_(favorite))
    if status:
        try:
            stmt = stmt.where(Clip.status == ClipStatus(status))
        except ValueError as exc:
            raise HTTPException(422, f"unknown status: {status}") from exc
    if cursor:
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            ts_raw, id_raw = raw.split("|", 1)
            stmt = stmt.where(
                tuple_(Clip.captured_at, Clip.id)
                < (datetime.fromisoformat(ts_raw), UUID(id_raw))
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "malformed cursor") from exc

    rows = list(db.scalars(stmt).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        token = f"{last.captured_at.isoformat()}|{last.id}"
        next_cursor = base64.urlsafe_b64encode(token.encode()).decode()

    return ClipPage(items=[_clip_out(c) for c in rows], next_cursor=next_cursor)


@app.patch(
    "/api/clips/{clip_id}", response_model=ClipOut, response_model_by_alias=True
)
def patch_clip(
    clip_id: UUID,
    payload: ClipPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> ClipOut:
    """Rename, pin, or change visibility.

    Only keys present in the request body are touched, so a rename cannot
    accidentally reset visibility to the schema default.
    """
    clip = db.get(Clip, clip_id)
    if clip is None or clip.deleted_at is not None or clip.owner_id != user.id:
        # Not 403: telling someone their key is valid but the clip is not
        # theirs confirms the clip exists.
        raise HTTPException(404, "clip not found")

    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        title = (changes["title"] or "").strip()
        clip.title = title or None
    if "favorite" in changes:
        clip.favorite = bool(changes["favorite"])
    if "visibility" in changes and changes["visibility"] is not None:
        try:
            clip.visibility = Visibility(changes["visibility"])
        except ValueError as exc:
            raise HTTPException(
                422, f"unknown visibility: {changes['visibility']}"
            ) from exc

    db.commit()
    db.refresh(clip)
    return _clip_out(clip)


@app.post(
    "/api/clips/{clip_id}/trim", response_model=ClipOut, response_model_by_alias=True
)
def trim_clip(
    clip_id: UUID,
    payload: ClipTrim,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> ClipOut:
    """Shorten a clip in place.

    Destructive by design: the point of trimming is that the 25 seconds of
    walking back to lane stop existing, rather than being kept alongside.
    """
    clip = db.get(Clip, clip_id)
    if clip is None or clip.deleted_at is not None or clip.owner_id != user.id:
        # Not 403: telling someone their key is valid but the clip is not
        # theirs confirms the clip exists.
        raise HTTPException(404, "clip not found")
    if payload.end_ms <= payload.start_ms:
        raise HTTPException(422, "end must be after start")

    source = db.get(ClipRendition, (clip_id, "source"))
    if source is None or not source.storage_key or source.status != RenditionStatus.ready:
        raise HTTPException(409, "clip has no playable source to trim")

    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        # Reading the object back would mean downloading it, trimming, and
        # re-uploading. Worth building when there is a reason to; saying so
        # beats failing in a way that looks like a bug.
        raise HTTPException(501, "trimming is only implemented for local storage")

    source_path = storage.path_for(source.storage_key)
    if not source_path.exists():
        raise HTTPException(409, "source file is missing from storage")

    start_s = payload.start_ms / 1000
    end_s = min(payload.end_ms / 1000, clip.duration_ms / 1000)
    trimmed = source_path.with_suffix(".trimmed.mp4")
    try:
        trim(source_path, trimmed, start_s, end_s)
        actual = duration_seconds(trimmed)
        # Replace only once the new file is known good, so a failed trim never
        # destroys the clip it was editing.
        trimmed.replace(source_path)
    except (MediaError, OSError) as exc:
        trimmed.unlink(missing_ok=True)
        raise HTTPException(422, f"could not trim: {exc}") from exc

    clip.duration_ms = int(actual * 1000)
    source.bytes = source_path.stat().st_size
    # The key is unchanged, so this is the only thing telling anything holding a
    # cached copy that the file behind it is different now.
    clip.version += 1

    thumb = db.get(ClipRendition, (clip_id, "thumb"))
    if thumb is not None and thumb.storage_key:
        thumb_path = storage.path_for(thumb.storage_key)
        if poster(source_path, thumb_path, actual / 2):
            thumb.bytes = thumb_path.stat().st_size
            thumb.status = RenditionStatus.ready

    db.commit()
    db.refresh(clip)
    return _clip_out(clip)


@app.delete("/api/clips/{clip_id}", status_code=204)
def delete_clip(
    clip_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    clip = db.get(Clip, clip_id)
    if clip is None or clip.deleted_at is not None or clip.owner_id != user.id:
        # Not 403: telling someone their key is valid but the clip is not
        # theirs confirms the clip exists.
        raise HTTPException(404, "clip not found")
    storage = get_storage()
    for rendition in clip.renditions:
        if rendition.storage_key:
            storage.delete(rendition.storage_key)
    clip.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- share page


@app.get("/c/{slug}", response_class=HTMLResponse)
def share_page(
    slug: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    clip = db.scalar(
        select(Clip).where(Clip.public_slug == slug, Clip.deleted_at.is_(None))
    )
    if clip is None:
        raise HTTPException(404, "clip not found")

    storage = get_storage()
    by_label = {r.label: r for r in clip.renditions}
    source = by_label.get("source")
    thumb = by_label.get("thumb")

    # A view is an event, not a synchronous write to the clips row. Batching it
    # through the event pipeline is the real design; incrementing inline keeps
    # the prototype honest about where the counter comes from.
    clip.view_count += 1
    db.commit()

    meta = clip.capture_meta or {}
    ready = RenditionStatus.ready
    return templates.TemplateResponse(
        request,
        "clip.html",
        {
            "clip": clip,
            "captured_at": _as_utc(clip.captured_at),
            "uploaded_at": _as_utc(clip.uploaded_at),
            "video_url": _rendition_url(clip, source)
            if source and source.status == ready
            else None,
            "thumb_url": _rendition_url(clip, thumb)
            if thumb and thumb.status == ready
            else None,
            "share_url": _share_url(clip.public_slug),
            "width": meta.get("width", 1920),
            "height": meta.get("height", 1080),
            "duration_s": round(clip.duration_ms / 1000, 1),
            "size_mb": round((clip.source_bytes or 0) / 1_048_576, 1),
            "meta": meta,
        },
    )


@app.get("/", response_class=HTMLResponse)
def library_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    page = list_clips(cursor=None, limit=60, db=db)
    return templates.TemplateResponse(request, "library.html", {"clips": page.items})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "storage": settings.storage_backend}


# ------------------------------------------------ local dev storage endpoints
# Only mounted when STORAGE_BACKEND=local. With R2 these do not exist and the
# bytes never touch this process at all.

if settings.storage_backend == "local":

    @app.put("/dev-upload")
    async def dev_upload(
        request: Request, key: str, expires: int, sig: str
    ) -> dict[str, int]:
        storage = get_storage()
        assert isinstance(storage, LocalStorage)
        if not storage.verify(key, expires, sig):
            raise HTTPException(403, "bad or expired upload signature")
        body = await request.body()
        if not body:
            raise HTTPException(400, "empty body")
        try:
            written = storage.write(key, body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"bytes": written}

    @app.get("/files/{key:path}")
    def dev_file(key: str) -> FileResponse:
        storage = get_storage()
        assert isinstance(storage, LocalStorage)
        try:
            target = storage.path_for(key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not target.exists():
            raise HTTPException(404, "not found")
        # FileResponse honours Range requests, which is what lets a player seek.
        return FileResponse(target)


# --------------------------------------------------------------- built SPA
# `npm run build` in frontend/ emits here. When it exists the manager app is
# served from this process at /app, so production is a single origin and the
# CORS allowance above becomes dev-only.

_SPA_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _SPA_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=str(_SPA_DIST), html=True), name="spa")
