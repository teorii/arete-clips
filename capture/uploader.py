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


class UploadError(RuntimeError):
    pass


class Uploader:
    def __init__(self, api_base_url: str, journal_path: Path, timeout: float = 60.0):
        self.api = api_base_url.rstrip("/")
        self.journal_path = Path(journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    # -------------------------------------------------------------- journal

    def _load(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        try:
            return json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
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
            except OSError:
                pass  # a locked file is not worth failing a finished upload

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
            except OSError:
                pass
        return removed

    # ------------------------------------------------------------- plumbing

    def _post_json(self, client: httpx.Client, url: str, body: dict) -> dict:
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = client.post(url, json=body)
                if response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
                last = UploadError(f"{response.status_code} {response.text[:200]}")
            except httpx.HTTPStatusError as exc:
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
