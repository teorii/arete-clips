# Clipper

Press F9, get a link to the last 30 seconds of your game.

A gameplay clip service built the way the content actually flows: capture
happens on the player's machine, the frame never leaves GPU memory, and nothing
is stored anywhere until a human decides the moment mattered.

## How it works

```
  Game renders                                    ~1.5 MB/s compressed
       |                                                   |
       v                                                   v
  ddagrab (Desktop Duplication)  --D3D11 frame-->  h264_nvenc  -->  ring buffer
       |                          stays on GPU                      60s on disk
       |                                                                 |
       |                                                          F9 pressed
       |                                                                 v
       |                                                    concat last 30s
       |                                                    remux, -c copy
       |                                                                 |
       v                                                                 v
  POST /api/clips  -->  presigned PUT  -->  object storage  -->  POST /complete
       |                     (direct, never through the API)             |
       v                                                                 v
   clips + clip_renditions                                    http://.../c/<slug>
```

The frame is never copied to system RAM uncompressed. At 1440p60 that copy
would be roughly 900 MB/s across PCIe; the compressed stream is about 1.5 MB/s.
That difference is the whole reason a tool like this can run during a game
without the player noticing.

The cut is a byte copy. Every buffer segment is forced to open on an IDR frame,
so trimming to a segment boundary means concatenating compressed data with
`-c copy`: no decode, no re-encode.

## Measured

One run on an RTX 3070, capturing a 2560x1440 display at 60 fps, 12 Mbps:

| Stage | Time |
|---|---|
| Cut (snapshot segments, concat, remux, poster frame, sha256) | 1.77 s |
| Upload (44.1 MB, local backend) | 3.14 s |
| **Hotkey to shareable link** | **4.92 s** |

The clip came out at 30.9 s and 44.1 MB, and ring segments held steady at
3.08 MB per 2 s, which is 12.3 Mbps against a 12 Mbps target.

Two caveats on those numbers. The upload figure is loopback, not a residential
uplink, so it says nothing yet about the real time-to-link. And the cut is
dominated by the work around the remux (copying ~50 MB of segments, hashing the
result for dedup, extracting a poster frame) rather than by the remux itself.

## Layout

| Path | What it is |
|---|---|
| `capture/ringbuffer.py` | The rolling NVENC buffer. `-segment_wrap` makes the directory a self-overwriting ring. |
| `capture/clipper.py` | Snapshot the newest segments, concat, remux to MP4, make a poster frame. |
| `capture/uploader.py` | Three-step upload against the presigned-URL contract, journalled to disk for crash recovery. |
| `capture/hotkey.py` | Global hotkey via `RegisterHotKey`, deliberately not a low-level keyboard hook. |
| `capture/daemon.py` | Ties it together. Entry point. |
| `server/main.py` | Clip API and share pages. Never touches video bytes in production. |
| `server/models.py` | SQLAlchemy schema. |
| `server/storage.py` | Storage behind one interface: local disk for dev, Cloudflare R2 for real. |
| `sql/001_init.sql` | Canonical Postgres schema for when `DATABASE_URL` points at Supabase. |
| `frontend/src/App.tsx` | Clip manager: grid, search, pinned filter, recent links. |
| `frontend/src/ClipDetail.tsx` | Player with rename, pin, copy, delete and arrow-key navigation. |
| `frontend/src/useClips.ts` | Cursor-paginated list with stale-response guarding. |

## Setup

```bash
winget install --id Gyan.FFmpeg -e
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Find the display you play on, then set `DDAGRAB_OUTPUT_IDX` in `.env`:

```bash
.venv\Scripts\python -m capture.daemon --probe
```

## Run it as an app

Double-click **`Clipper.bat`**. That opens a native window with the API and the
capture daemon running inside the same process, so there is nothing to start in
a terminal and no URL to type.

- **F9** clips the last 30 seconds from anywhere, including inside a game.
- **Closing the window does not stop recording.** It hides to the tray, because
  a recorder that quits when you close its window is no use mid-game. Quit for
  real from the tray menu.
- The **tray icon** doubles as a status light: red means the ring buffer is
  running, grey means capture could not start and only the library works.
  Right-click it to clip, reopen, or quit. Windows 11 files new tray icons
  under the `^` overflow, so drag it onto the taskbar to keep it visible.
- `Clipper (debug).bat` is the same thing with a console attached, which is
  where you watch clip timings and the share link appear.
- Logs land in `clipper.log` when launched without a console.

The window is WebView2, the browser engine already built into Windows 11, so
there is no second runtime to install and nothing bundled.

```bash
python desktop.py                 # window plus capture
python desktop.py --no-capture    # window only, for UI work
python desktop.py --verbose       # log every HTTP request, open devtools
python desktop.py --no-tray       # no tray; closing the window quits
```

## Run the pieces separately

Useful for development. Two terminals.

```bash
.venv\Scripts\python -m uvicorn server.main:app --port 8000
```

```bash
.venv\Scripts\python -m capture.daemon
```

Press **F9** in game. The link is printed and copied to your clipboard.

`--test` warms the buffer, takes one clip and exits, which is the fastest way
to check the whole path without opening a game.

## The clip manager

`http://localhost:8000/app` is a React app for browsing what you have captured:
search, a pinned filter, inline rename, delete, and one-click copy of any share
link. Arrow keys move between clips inside the player, Escape closes it.

It is served by the FastAPI process from `frontend/dist`, so there is one
origin and nothing extra to run. Rebuild after changing it:

```bash
cd frontend && npm run build
```

For live reload while working on the app:

```bash
cd frontend && npm run dev
```

That runs on port 5173 and proxies `/api` to port 8000, so both modes use the
same relative URLs and no base URL needs configuring.

Two kinds of saved link, deliberately kept separate. **Pinned** is a column on
the clip, so it follows the clip across machines and reinstalls. **Recent
links** is the trail of what you actually copied, kept in browser storage,
which is the faster way back to something you pasted into Discord an hour ago.

`http://localhost:8000` still serves the plain server-rendered library, which
needs no build step.

## Going from prototype to real

Both swaps are configuration, not code.

**Postgres.** Run `sql/001_init.sql` against a Supabase project, then set
`DATABASE_URL=postgresql+psycopg://...`. SQLite is the zero-setup default so
the server boots with nothing installed.

**Object storage.** Create an R2 bucket, then set `STORAGE_BACKEND=r2` plus the
four `R2_*` values. The client code does not change: it already asks the API
where to put bytes and PUTs them there. The local backend exists to mirror that
contract, and it is the only place bytes pass through the app tier.

## What is deliberately not built yet

- **Audio.** `ddagrab` is video-only. Windows loopback capture needs a virtual
  audio device, and separate game/mic/voice tracks (free at capture time,
  irreversible if skipped) are the version worth building.
- **Byte-range resumable upload.** Retry is per-clip via the journal, so a crash
  or reboot does not lose a clip, but a 90%-complete upload restarts. The API is
  already shaped around upload targets so multipart drops in.
- **Lazy transcode.** One rendition on ingest, which is correct. The rest should
  be generated on first playback, since most clips are never watched.
- **Auth.** Every clip belongs to one hardcoded owner id, so the manager app
  trusts whoever opens it.
- **Proxy-first upload.** Encode a small 480p proxy, upload it first so the link
  is live in seconds, then send the source in the background throttled to a
  fraction of measured uplink while a game is in the foreground.
- **A sweeper** for clips stranded in `pending_upload` by a client that died
  between the hotkey and the first byte.
