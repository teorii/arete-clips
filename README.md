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

Sound comes from whatever Windows is playing to, and follows the default device
if you change it. ffmpeg cannot open a WASAPI device, so the samples are read in
Python and handed to the encoder on its stdin: one process timestamps the
picture and the sound, which is what keeps them together. Recording them
separately and muxing afterwards meant two clocks and a guess at the distance
between them, and the guess played every clip's sound early.

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
| `frontend/src/App.tsx` | Clip manager: grid, search, pinned filter, recent links. |
| `frontend/src/ClipDetail.tsx` | Player with rename, pin, copy, delete and arrow-key navigation. |
| `frontend/src/useClips.ts` | Cursor-paginated list with stale-response guarding. |
| `branding.py` | The name, the colours and the drawing of the app mark. One source for tray, window and executable. |
| `tools/make_icon.py` | Renders that mark to `assets/arete.ico` and the web favicon. |
| `tools/make_launcher.py` | Builds `.venv\Scripts\Arete.exe`, the launcher that gives Task Manager the name and icon. |

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

## Setup

Run `Arete.exe`. It asks three things and then records:

- **Where clips live.** This PC by default, which needs nothing installed: a
  SQLite file and a folder, both under `%APPDATA%\Arete`. The alternative is
  sending them to someone else's Arete, which needs their address and an
  invite code.
- **What to record.** A running program, or a display directly. Capture is per
  display, so picking a program just selects the display it is on.
- **How much.** Clip length and the hotkey.

There is no key to obtain. A hosting install creates its own account on first
start, and prints an address and invite code so another machine can join it.

## Settings

Reachable from the tray. The first three sections change what the app needs to
run and are written to `config.env`. The fourth is preferences: how the app
should behave, kept in `preferences.json` beside it.

| Preference | Default | What it changes |
|---|---|---|
| Play a sound when a clip is taken | on | The only confirmation you get with a game in front |
| Show tray notifications | on | Balloons, which a fullscreen game covers anyway |
| Fully close when pressing X | off | On quits instead of minimizing to the tray and continuing to record |
| Copy a link as soon as it is made | on | Off leaves the link on screen to copy yourself |
| Ask twice before deleting a clip | on | Off makes the trash immediate |
| Warn when unshared clips pass | 2 GB | When the held-storage bar turns red |

Both files live in `%APPDATA%\Arete`. Preferences are read when used rather
than cached, so a change applies to the next clip rather than the next launch.
Unknown keys are ignored and missing ones take their default, so a file written
by a different version still loads.

## Sharing

The app opens a tunnel so links work off this machine, and closes it on quit.
`MANAGE_TUNNEL=false` turns that off, and links then only resolve locally.

A quick tunnel gets a **new hostname every launch**, so links from a previous
session stop working. That is the one thing to know before sending anyone a
link you expect to last.

Clip files are served from the same host, so a link carries both the page and
the video. Anyone with the link can watch; listing the library needs a key.

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

## What is deliberately not built yet

- **Microphone and voice chat on separate tracks.** Desktop sound is recorded,
  and only desktop sound: what the game played, not what the room heard.
- **Byte-range resumable upload.** Retry is per-clip via the journal, so a crash
  or reboot does not lose a clip, but a 90%-complete upload restarts. The API is
  already shaped around upload targets so multipart drops in.
- **Lazy transcode.** One rendition on ingest, which is correct. The rest should
  be generated on first playback, since most clips are never watched.
- **Anything past the API key.** A key identifies a machine and libraries are
  separate because of it, but there is no password, no way to revoke a key
  short of editing the database, and a share link is public to whoever holds
  it. Per-clip visibility is unbuilt.
- **Proxy-first upload.** Encode a small 480p proxy, upload it first so the link
  is live in seconds, then send the source in the background throttled to a
  fraction of measured uplink while a game is in the foreground.
- **A sweeper** for clips stranded in `pending_upload` by a client that died
  between the hotkey and the first byte.
