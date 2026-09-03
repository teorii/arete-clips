"""Clip API and share pages.

No endpoint here accepts video bytes in production. The client asks for a place
to put them, PUTs directly to object storage, then reports back. This service
only ever moves metadata.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from .auth import generate_key, hash_key, require_user
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
    RegisterOut,
    RegisterRequest,
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


@app.post("/api/register", response_model=RegisterOut, response_model_by_alias=True)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> RegisterOut:
    """Create an account and hand back its key.

    Exists so a new machine needs one shared invite code rather than a key
    issued by hand on the host. Libraries are separate per user, so someone
    joining never sees anyone else's clips; what they do share is the host's
    disk, which is why an invite code gates this at all.

    Blank invite_code on the server means registration is closed.
    """
    expected = settings.invite_code.strip()
    if not expected:
        raise HTTPException(403, "this server is not accepting new accounts")
    # compare_digest, so a wrong guess cannot be narrowed down by timing.
    if not secrets.compare_digest(payload.invite.strip(), expected):
        raise HTTPException(403, "that invite code is not right")

    handle = payload.handle.strip()
    if not handle:
        raise HTTPException(422, "pick a name")
    if db.scalar(select(User).where(User.handle == handle)):
        raise HTTPException(409, f"the name {handle!r} is taken on this server")

    key = generate_key()
    db.add(User(id=uuid7(), handle=handle, api_key_hash=hash_key(key)))
    db.commit()
    return RegisterOut(handle=handle, api_key=key)


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
    start_s = payload.start_ms / 1000
    end_s = min(payload.end_ms / 1000, clip.duration_ms / 1000)

    # Works for a folder or a bucket: the bytes come back, get cut, and go out
    # again under the same key. On a self-hosted setup the round trip is local.
    with tempfile.TemporaryDirectory(prefix="trim_") as work:
        working = Path(work)
        original = working / "source.mp4"
        cut = working / "cut.mp4"

        if not storage.download(source.storage_key, original):
            raise HTTPException(409, "source file is missing from storage")

        try:
            trim(original, cut, start_s, end_s)
            actual = duration_seconds(cut)
        except (MediaError, OSError) as exc:
            # Nothing has been written back yet, so the clip is untouched.
            raise HTTPException(422, f"could not trim: {exc}") from exc

        source.bytes = storage.upload(source.storage_key, cut, "video/mp4")

        thumb = db.get(ClipRendition, (clip_id, "thumb"))
        if thumb is not None and thumb.storage_key:
            poster_file = working / "poster.jpg"
            if poster(cut, poster_file, actual / 2):
                thumb.bytes = storage.upload(
                    thumb.storage_key, poster_file, "image/jpeg"
                )
                thumb.status = RenditionStatus.ready

    clip.duration_ms = int(actual * 1000)
    # The key is unchanged, so this is the only thing telling anything holding a
    # cached copy that the file behind it is different now.
    clip.version += 1

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


@app.get("/")
def root() -> RedirectResponse:
    """There is one library, and it is the app."""
    return RedirectResponse("/app/")


@app.get("/api/held/{name}")
def held_clip_file(name: str, expires: int = Query(...), sig: str = Query(...)) -> FileResponse:
    """A clip captured on this machine that has no link yet.

    Held clips exist only as files next to the app: the server has never heard
    of them, which is the point of holding them. Deciding whether one is worth
    a link meant guessing from a thumbnail, so the library can play them from
    here instead.

    Only files directly inside the capture output directory are served, and the
    name is used as a name rather than a path, so nothing outside it is
    reachable however the request is spelled.
    """
    from paths import data_dir

    from .storage import verify_held

    if not verify_held(Path(name).name, expires, sig):
        raise HTTPException(status_code=404, detail="No such clip.")

    folder = (data_dir() / "clips_out").resolve()
    target = (folder / Path(name).name).resolve()
    if target.parent != folder or not target.is_file():
        raise HTTPException(status_code=404, detail="No such clip.")
    return FileResponse(target, media_type="video/mp4")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Reachability, nothing more. Setup uses it to tell a real server from a
    hostname that merely resolves."""
    return {"status": "ok", "app": "arete"}


# ------------------------------------------------------------ file endpoints
# How clip bytes get in and out. The upload URL is signed and short lived,
# which is what lets another machine upload here with no shared credential.

@app.put("/upload")
async def upload(
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
def serve_file(key: str) -> FileResponse:
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
