# Arete

Press F9, get a shareable link to the last 30 seconds of gameplay. Capture runs
on the player's GPU; the backend only ever moves metadata.

## What this is

A 24-hour demo, built once, used by one person. It is not a service, it will
not have hundreds of users, and it does not need to survive traffic. The goal
is a small application that works reliably every time, not a small version of
a large system.

`docs/design/medal-question.html` is the design authority for *structure*: when
a question comes up about how the pieces fit together, check there. It is not a
backlog. That document is written for 10M MAU and 1.2M clips a day, and most of
what it calls for is the correct answer to a problem this project does not have.

Do not propose or build: dedup, storage tiering, retention policies, lazy
transcode, event pipelines, ML flywheels, auth, multi-region, resumable
byte-range uploads, kill switches, or staged rollout. If a suggestion is
justified by scale, cost at volume, or "when this grows", it is out of scope.
Say so and move on.

Worth doing is anything that makes the thing in front of you work better: it
captures what you asked for, it tells you when it fails, and the link plays.

## Running it

The shipped form is the desktop app: `Arete.bat`, or `python desktop.py`.
That runs the API, the capture daemon and a WebView2 window in one process.
`desktop.py --verbose` adds access logs and devtools; `--no-capture` skips
recording; `--no-tray` makes closing the window quit. The pieces below still run standalone for development.

| Part | Command | Notes |
|---|---|---|
| Desktop app | `python desktop.py` | API + capture + window, one process |
| API + share pages | `.venv\Scripts\python -m uvicorn server.main:app --port 8000` | Also serves the built SPA at `/app` |
| Capture daemon | `.venv\Scripts\python -m capture.daemon` | `--test` clips once and exits; `--probe` lists displays |
| Frontend (dev) | `cd frontend && npm run dev` | Port 5173, proxies `/api` to 8000 |
| Frontend (build) | `cd frontend && npm run build` | Emits `frontend/dist`, which the server mounts |
| Lint | `cd frontend && npm run lint` | oxlint, not eslint |
| Icon + launcher | `.venv\Scripts\python tools\make_icon.py` then `tools\make_launcher.py` | Rebuild after touching `branding.py` |

Requires ffmpeg on PATH (or installed via winget) and an NVENC-capable GPU.

## Where things run

Self-hosted, no SaaS. Postgres on 5432 (`arete`), MinIO on 9000 (bucket
`clips`), app on 8000. `desktop.py` preflights both and names whichever is
down. `scripts\start-storage.bat` starts MinIO; Postgres is a Windows service.

`server/models.py` is the schema source of truth and `server/migrate.py` builds
it. `sql/001_init.sql` is a reference that records reasoning, not something that
runs; if they disagree, the models are right.

The storage backend is S3-shaped on purpose: MinIO locally and R2 or S3 on a
server are the same code, which is what keeps this deployable off this desktop.

## Identity

Every client carries an API key (`X-API-Key`), issued by `tools/add_user.py`.
Only the hash is stored. `owner_id` on a clip comes from the key, and the
library query filters by it, so libraries are separate by default.

Public on purpose: `/c/<slug>`, the rendition files, `/healthz`. Everything
else needs a key. Acting on another user's clip returns 404, not 403, because
403 confirms the clip exists.

`desktop.py` decides whether to host the API from whether `API_BASE_URL` is
local. A second machine points it at the host and starts no server of its own.

## Invariants worth protecting

These are load-bearing. Changing one is a design decision, not a refactor.

- **Video bytes never pass through the API tier.** The client asks for an upload
  target, PUTs directly to storage, then confirms. The only exception is
  `LocalStorage` in dev, and it is confined to that class.
- **Capture stays GPU-resident.** `ddagrab` yields D3D11 frames straight to
  `h264_nvenc`. Never add `hwdownload`, a CPU encoder, or a pixel-format filter
  that forces a readback: that is ~900 MB/s across PCIe instead of ~1.5 MB/s.
- **The cut is a remux, never a transcode.** Every segment opens on an IDR
  frame, so concatenation uses `-c copy`. If you find yourself re-encoding to
  trim, the keyframe interval is the thing to change.
- **Keyset pagination, never OFFSET.** `list_clips` orders by
  `(captured_at desc, id desc)` and seeks with a cursor.
- **Two clocks, and only one is trusted.** `captured_at` comes from the player's
  machine and may lag days or be wrong; `uploaded_at` is ours. Clamp the former
  against the latter.
- **Timestamps leave the API timezone-aware.** SQLite has no timezone type, so
  values round-trip naive. `_as_utc` in `server/main.py` stamps UTC on the way
  out. Without it every displayed time silently shifts by the viewer's offset.
- **The hotkey uses `RegisterHotKey`, not a keyboard hook.** League runs
  Vanguard, and a `WH_KEYBOARD_LL` hook is behaviourally a keylogger.
- **The name and mark live in `branding.py`.** Tray, window, favicon and
  executable resources all render from it. Windows names a running app after
  the FileDescription of its executable, not its window title, which is the
  only reason `.venv\Scripts\Arete.exe` exists: launched through plain
  pythonw.exe the app is "Python" in Task Manager whatever the window says.
  Rerun `tools/make_launcher.py` after changing the mark.

## Layout

```
branding.py The name, colours and app mark. One source for every surface.
tools/      make_icon.py renders the mark, make_launcher.py builds Arete.exe
assets/     arete.ico, the icon Windows reads for taskbar and Task Manager
server/     FastAPI. main.py routes, models.py schema, storage.py backends
capture/    Windows client. ringbuffer.py -> cutter.py -> uploader.py
frontend/   React + Vite clip manager, served at /app
sql/        Canonical Postgres DDL for the Supabase swap
docs/design/  The system design this implements
```

## Request lifecycle

`capture.daemon` F9 -> `cutter.flush` (snapshot segments, concat, remux) ->
`uploader.submit` -> `POST /api/clips` (row + presigned target) -> PUT bytes to
storage -> `POST /api/clips/{id}/complete` (server verifies size against
storage, flips status to `ready`) -> `GET /c/{slug}`.

## Conventions

- Python: snake_case modules, type hints throughout, `from __future__ import
  annotations`. Settings via `pydantic-settings` reading `.env`.
- TypeScript: named exports, `type` imports, no default exports except `App`.
  `erasableSyntaxOnly` is on, so no constructor parameter properties or enums.
- Errors: raise `HTTPException` in routes; the capture client catches narrow
  exception types and keeps failed uploads in the journal rather than dropping.
- Storage keys, never URLs, in the database. Domains and signing schemes change.

## Swapping the prototype pieces

Both are env vars in `.env`, not code changes:

- **Postgres**: run `sql/001_init.sql` against Supabase, set `DATABASE_URL=postgresql+psycopg://...`
- **Object storage**: set `STORAGE_BACKEND=r2` plus the four `R2_*` values

## Tests

`.venv\Scripts\python -m pytest` runs 34 tests covering the clip
lifecycle, upload-signature enforcement and ring buffer segment selection.
Capture itself is not covered: it needs a GPU and a live display.

## Actual gaps, in order

1. **No audio.** `ddagrab` is video-only and Windows ships no loopback device.
   The code to use one is written and dormant: install a device providing
   `virtual-audio-capturer` and it is picked up automatically. Deferred on
   purpose, not forgotten.
2. **Links only resolve on this machine.** `BIND_HOST=0.0.0.0` plus a LAN
   address in `PUBLIC_BASE_URL` covers the same network. Anything wider needs
   `STORAGE_BACKEND=r2` and a host. Note there is no auth, so binding wide
   exposes viewing and deleting to whoever can reach the port.

Done and worth not re-litigating: trimming (server-side remux, keyframe
snapped), capture-failure detection (a wedged encoder counts, not just a dead
one), and staging cleanup (clips used to be stored twice).

Not gaps, deliberately: auth, packaging, and everything in the do-not-build
list above.
