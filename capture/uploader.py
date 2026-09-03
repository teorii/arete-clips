"""Ship a finished clip to the service.

Three steps, always the same shape regardless of where the bytes end up:
reserve a clip row and get somewhere to put the file, PUT the file at that URL,
then confirm. The API never sees a video byte in production.

Uploads are journalled to disk before they start, so a crash, a reboot, or a
multi-day offline gap does not lose the clip. Retry is at clip granularity
rather than byte range; true resumable multipart is the next step and is why
the API is shaped around upload targets instead of a single POST.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from problems import warn

from .cutter import ClipResult


@dataclass
class PendingUpload:
    mp4_path: str
    thumb_path: str | None
    duration_ms: int
    captured_at: str
    content_hash: str
    source_bytes: int
    capture_meta: dict
    title: str | None = None
    # True while the clip is waiting for someone to ask for a link. Distinct
    # from a failed upload, which should be retried on its own.
    awaiting_user: bool = False


class UploadError(RuntimeError):
    pass


class Uploader:
    def __init__(
        self,
        api_base_url: str,
        journal_path: Path,
        timeout: float = 60.0,
        api_key: str = "",
    ):
        self.api = api_base_url.rstrip("/")
        self.api_key = api_key
        self.journal_path = Path(journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    # -------------------------------------------------------------- journal

    def _load(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        try:
            return json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Returning an empty list here would drop every clip waiting to be
            # shared, silently, and the next write would overwrite the evidence.
            # Move it aside instead: the clips are still on disk, and a named
            # file is something a person can look at.
            spoiled = self.journal_path.with_suffix(".corrupt")
            try:
                self.journal_path.replace(spoiled)
            except OSError as move_failure:
                warn("journal", move_failure, "could not set the bad file aside")
            warn(
                "journal",
                exc,
                f"clips awaiting a link are not listed; the old file is at {spoiled}",
            )
            return []

    def _save(self, entries: list[dict]) -> None:
        tmp = self.journal_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        tmp.replace(self.journal_path)

    def _enqueue(self, pending: PendingUpload) -> None:
        entries = self._load()
        entries.append(asdict(pending))
        self._save(entries)

    def _dequeue(self, mp4_path: str) -> None:
        self._save([e for e in self._load() if e.get("mp4_path") != mp4_path])

    def pending_count(self) -> int:
        return len(self._load())

    # --------------------------------------------------------------- upload

    def hold(
        self,
        clip: ClipResult,
        captured_at: datetime,
        capture_meta: dict,
        title: str | None = None,
    ) -> str:
        """Keep a clip locally without uploading it.

        Capture and publishing are separate decisions: most clips are worth
        keeping for a minute and never worth sharing, and uploading every one
        spends bandwidth during the game to store things nobody asked for.
        """
        pending = self._pending_from(clip, captured_at, capture_meta, title)
        pending.awaiting_user = True
        self._enqueue(pending)
        return pending.mp4_path

    def _pending_from(
        self,
        clip: ClipResult,
        captured_at: datetime,
        capture_meta: dict,
        title: str | None,
    ) -> PendingUpload:
        return PendingUpload(
            mp4_path=str(clip.mp4_path),
            thumb_path=str(clip.thumb_path) if clip.thumb_path else None,
            duration_ms=clip.duration_ms,
            captured_at=captured_at.astimezone(timezone.utc).isoformat(),
            content_hash=clip.content_hash,
            source_bytes=clip.size_bytes,
            capture_meta=capture_meta,
            title=title,
        )

    def waiting(self) -> list[dict]:
        """Clips held locally, newest first."""
        entries = [e for e in self._load() if e.get("awaiting_user")]
        entries.sort(key=lambda e: e.get("captured_at", ""), reverse=True)
        return entries

    def publish(self, mp4_path: str) -> str:
        """Upload one held clip and return its share URL."""
        for entry in self._load():
            if entry.get("mp4_path") == mp4_path:
                pending = PendingUpload(**entry)
                pending.awaiting_user = False
                return self._send(pending)
        raise UploadError("that clip is no longer waiting to be shared")

    def discard(self, mp4_path: str) -> None:
        """Drop a held clip and delete its files."""
        for entry in self._load():
            if entry.get("mp4_path") == mp4_path:
                self._discard_local(
                    Path(entry["mp4_path"]),
                    Path(entry["thumb_path"]) if entry.get("thumb_path") else None,
                )
        self._dequeue(mp4_path)

    def rename(self, mp4_path: str, title: str | None) -> bool:
        """Retitle a held clip.

        Stored on the journal entry, so the name travels with the clip and is
        what the server is told when a link is finally generated. Renaming
        before sharing is the point: the game name the app guessed is a
        starting point, not the description you want on the link.
        """
        entries = self._load()
        cleaned = (title or "").strip() or None
        for entry in entries:
            if entry.get("mp4_path") == mp4_path:
                entry["title"] = cleaned
                self._save(entries)
                return True
        return False

    def discard_all(self) -> int:
        """Drop every held clip. Returns how many went.

        Failed uploads are left alone: those are the journal doing its job, and
        losing them would defeat the point of having one.
        """
        removed = 0
        for entry in list(self._load()):
            if entry.get("awaiting_user"):
                self.discard(entry["mp4_path"])
                removed += 1
        return removed

    def submit(
        self,
        clip: ClipResult,
        captured_at: datetime,
        capture_meta: dict,
        title: str | None = None,
    ) -> str:
        pending = PendingUpload(
            mp4_path=str(clip.mp4_path),
            thumb_path=str(clip.thumb_path) if clip.thumb_path else None,
            duration_ms=clip.duration_ms,
            captured_at=captured_at.astimezone(timezone.utc).isoformat(),
            content_hash=clip.content_hash,
            source_bytes=clip.size_bytes,
            capture_meta=capture_meta,
            title=title,
        )
        self._enqueue(pending)
        return self._send(pending)

    def retry_pending(self) -> list[str]:
        """Re-send anything a previous run left behind."""
        shared: list[str] = []
        for entry in self._load():
            pending = PendingUpload(**entry)
            if pending.awaiting_user:
                continue  # held on purpose, not a failure to retry
            if not Path(pending.mp4_path).exists():
                self._dequeue(pending.mp4_path)  # local file is gone, drop it
                continue
            try:
                shared.append(self._send(pending))
            except (UploadError, httpx.HTTPError):
                continue  # stays journalled, retried next start
        return shared

    def _send(self, pending: PendingUpload) -> str:
        mp4 = Path(pending.mp4_path)
        thumb = Path(pending.thumb_path) if pending.thumb_path else None
        has_thumb = thumb is not None and thumb.exists()

        with httpx.Client(timeout=self.timeout) as client:
            created = self._post_json(
                client,
                f"{self.api}/api/clips",
                {
                    "durationMs": pending.duration_ms,
                    "capturedAt": pending.captured_at,
                    "sourceBytes": pending.source_bytes,
                    "contentHash": pending.content_hash,
                    "captureMeta": pending.capture_meta,
                    "triggerType": "hotkey",
                    "title": pending.title,
                    "hasThumbnail": has_thumb,
                },
            )

            uploads = created["uploads"]
            self._put_file(client, uploads["source"], mp4)
            labels = ["source"]
            if has_thumb and "thumb" in uploads:
                self._put_file(client, uploads["thumb"], thumb)
                labels.append("thumb")

            self._post_json(
                client,
                f"{self.api}/api/clips/{created['clipId']}/complete",
                {"labels": labels},
            )

        self._dequeue(pending.mp4_path)
        # The bytes are in object storage now, so the staging copy is a second
        # full-size copy of every clip. Without this the disk grows by roughly
        # 44 MB per clip forever, and deleting a clip in the library frees only
        # half of it.
        self._discard_local(mp4, thumb)
        return created["shareUrl"]

    @staticmethod
    def _discard_local(*paths: Path | None) -> None:
        for path in paths:
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                warn("staging", exc, f"{path.name} is still using disk")

    def sweep_staging(self, directory: Path) -> int:
        """Delete staging files no longer waiting to be uploaded.

        Anything in the staging directory that the journal does not know about
        has either already been uploaded or was orphaned by a crash. Safe to run
        at start-up, when nothing is in flight, and it clears leftovers from
        before uploads started cleaning up after themselves.
        """
        directory = Path(directory)
        if not directory.is_dir():
            return 0
        keep = {
            Path(entry[key]).resolve()
            for entry in self._load()
            for key in ("mp4_path", "thumb_path")
            if entry.get(key)
        }
        removed = 0
        for path in list(directory.glob("*.mp4")) + list(directory.glob("*.jpg")):
            if path.resolve() in keep:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                warn("staging", exc, f"could not remove {path.name}")
        return removed

    # ------------------------------------------------------------- plumbing

    def _auth_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _post_json(self, client: httpx.Client, url: str, body: dict) -> dict:
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = client.post(url, json=body, headers=self._auth_headers())
                if response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
                last = UploadError(f"{response.status_code} {response.text[:200]}")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    raise UploadError(
                        "rejected: check ARETE_API_KEY, or issue one with "
                        "python -m tools.add_user --handle <name>"
                    ) from exc
                raise UploadError(
                    f"{url} -> {exc.response.status_code} {exc.response.text[:200]}"
                ) from exc
            except httpx.HTTPError as exc:
                last = exc
            time.sleep(2**attempt)
        raise UploadError(f"{url} failed after retries: {last}")

    def _put_file(self, client: httpx.Client, target: dict, path: Path) -> None:
        data = path.read_bytes()
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = client.request(
                    target.get("method", "PUT"),
                    target["url"],
                    content=data,
                    headers=target.get("headers") or {},
                )
                if response.status_code < 400:
                    return
                if response.status_code < 500:
                    raise UploadError(
                        f"upload rejected: {response.status_code} {response.text[:200]}"
                    )
                last = UploadError(f"{response.status_code}")
            except httpx.HTTPError as exc:
                last = exc
            time.sleep(2**attempt)
        raise UploadError(f"upload of {path.name} failed after retries: {last}")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
