# Arete

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
| `capture/cutter.py` | Snapshot the newest segments, concat, remux to MP4, make a poster frame. |
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
| `branding.py` | The name, the colours and the drawing of the app mark. One source for tray, window and executable. |
| `tools/make_icon.py` | Renders that mark to `assets/arete.ico` and the web favicon. |
| `tools/make_launcher.py` | Builds `.venv\Scripts\Arete.exe`, the launcher that gives Task Manager the name and icon. |

## Setup

```bash
winget install --id Gyan.FFmpeg -e
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python tools\make_icon.py
.venv\Scripts\python tools\make_launcher.py
```

Find the display you play on, then set `DDAGRAB_OUTPUT_IDX` in `.env`:

```bash
.venv\Scripts\python -m capture.daemon --probe
```

## Run it as an app

Double-click **`Arete.bat`**. That opens a native window with the API and the
capture daemon running inside the same process, so there is nothing to start in
a terminal and no URL to type.

- **F9** clips the last 30 seconds from anywhere, including inside a game.
- **Closing the window does not stop recording.** It hides to the tray, because
  a recorder that quits when you close its window is no use mid-game. Quit for
  real from the tray menu.
- The **tray icon** doubles as a status light: the A is warm while the ring
  buffer is running and grey when capture could not start and only the library
  works. Right-click it to clip, reopen, or quit. Windows 11 files new tray
  icons under the `^` overflow, so drag it onto the taskbar to keep it visible.
- `Arete (debug).bat` is the same thing with a console attached, which is
  where you watch clip timings and the share link appear.
- Logs land in `arete.log` when launched without a console.

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

## Trimming

Open a clip, hit **Trim**, scrub to each point and set it. The cut is a remux:
`-ss`/`-to` with `-c copy`, so it is near-instant and lossless, and the file
genuinely shrinks rather than being played back with bounds.

The cost is granularity. Cuts land on keyframes, which capture places at
segment boundaries, so the start snaps back to the nearest one and the UI shows
where it will actually land rather than where you dropped the handle. Frame
accuracy would mean re-encoding the partial group of pictures at each edge.

Trimming replaces the clip. That is the point: the 25 seconds of walking back
to lane stop existing.

## Running it on a second machine

Every client carries an API key. It decides whose library a clip lands in, and
without one the API answers 401 to everything except share pages.

On the host, issue a key per person:

```bash
python -m tools.add_user --handle seth --adopt-existing
python -m tools.add_user --handle james
```

`--adopt-existing` takes ownership of clips captured before keys existed, so an
upgrade does not orphan a library. The key is printed once; only its SHA-256 is
stored, so it cannot be recovered, only reissued. `--revoke <handle>` disables
one without touching that person's clips.

Each machine puts its own key in `.env`:

```
ARETE_API_KEY=arete_...
API_BASE_URL=https://wherever-the-host-is
```

The second machine needs no server of its own. Pointing `API_BASE_URL` at the
host is enough: the app notices it is not the host, skips starting uvicorn, and
opens its window against the shared API. Mode is derived rather than being a
flag someone forgets to set.

What stays public, deliberately: `/c/<slug>` and the clip files themselves. A
share link that needed a key would not be a share link. Everything else, the
library listing included, needs one.

Libraries are separate. Acting on someone else's clip returns 404 rather than
403, since a 403 would confirm the clip exists.

## Sharing beyond this machine

Links resolve to `localhost` by default, which means they only work here. For
the same network, set `BIND_HOST=0.0.0.0` and point `PUBLIC_BASE_URL` at your
LAN address. There is no auth, so anyone who can reach the port can view and
delete clips: only do that on a network you trust.

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

- **Audio needs a loopback device.** Windows ships none, so `ddagrab` alone
  records silent video. Install
  [screen-capture-recorder](https://github.com/rdp/screen-capture-recorder-to-video-windows-free/releases)
  and its `virtual-audio-capturer` device is picked up automatically, no
  configuration. `AUDIO_DEVICE` in `.env` overrides the search: `none` forces
  silent video, or name an exact dshow device. Mic and voice chat on separate
  tracks are still unbuilt.
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
