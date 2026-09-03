# Clipper

Press F9, get a shareable link to the last 30 seconds of gameplay. Capture runs
on the player's GPU; the backend only ever moves metadata.

Personal project. The design authority is `docs/design/medal-question.html`:
when a structural question comes up, check there before inventing an answer.

## Three processes

| Part | Command | Notes |
|---|---|---|
| API + share pages | `.venv\Scripts\python -m uvicorn server.main:app --port 8000` | Also serves the built SPA at `/app` |
| Capture daemon | `.venv\Scripts\python -m capture.daemon` | `--test` clips once and exits; `--probe` lists displays |
| Frontend (dev) | `cd frontend && npm run dev` | Port 5173, proxies `/api` to 8000 |
| Frontend (build) | `cd frontend && npm run build` | Emits `frontend/dist`, which the server mounts |
| Lint | `cd frontend && npm run lint` | oxlint, not eslint |

Requires ffmpeg on PATH (or installed via winget) and an NVENC-capable GPU.

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

## Layout

```
server/     FastAPI. main.py routes, models.py schema, storage.py backends
capture/    Windows client. ringbuffer.py -> clipper.py -> uploader.py
frontend/   React + Vite clip manager, served at /app
sql/        Canonical Postgres DDL for the Supabase swap
docs/design/  The system design this implements
```

## Request lifecycle

`capture.daemon` F9 -> `clipper.flush` (snapshot segments, concat, remux) ->
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

## Known gaps

- **No tests.** This is application code and should have them. Start with
  `clipper.flush` segment selection and the `complete_clip` verification path.
- Video-only: no audio capture yet.
- Upload retry is per-clip via the journal, not byte-range resumable.
- One hardcoded `DEV_OWNER_ID`; no auth.
- Two `react(set-state-in-effect)` lint warnings in `useClips.ts` and
  `ClipDetail.tsx`. The `ClipDetail` one is better fixed with a `key` prop so
  the component remounts per clip.
