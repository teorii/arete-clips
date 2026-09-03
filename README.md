# Arete

Press F9, get a link to the last 30 seconds of your game.

A gameplay clip recorder for Windows. Capture runs on the player's GPU, the
frame never leaves video memory, and nothing is stored anywhere until a human
decides the moment mattered.

One executable. It records, hosts the clips, serves the share pages, and opens
its own tunnel so a link works off the machine. There is nothing to install
alongside it and no account to obtain.

**[Download Arete.exe](https://github.com/teorii/arete-clips/releases/latest)**
for Windows, or read on for how it works.

## Demo

https://github.com/user-attachments/assets/889149db-ca56-4dfe-af04-ae8a598359c5

A minute and a half with sound: recording during a game, watching a clip before
deciding it is worth sharing, generating a link, pasting it into Discord, and
opening the share page. Names and unrelated windows are blurred.

The same recording at full resolution is
[on the release](https://github.com/teorii/arete-clips/releases/download/v1.0.0/arete-demo.mp4).

## How it works

![How Arete works](docs/architecture.svg)

The frame is never copied to system RAM uncompressed. At 1440p60 that copy
would be roughly 900 MB/s across PCIe; the compressed stream is about 1.5 MB/s.
That difference is why this can run during a game without the player noticing.

The cut is a byte copy. Every ring segment is forced to open on an IDR frame,
so trimming to a segment boundary concatenates compressed data with `-c copy`:
no decode, no re-encode.

Sound is whatever Windows is playing to, and it follows the default device if
you change it. ffmpeg cannot open a WASAPI device, so the samples are read in
Python and handed to the encoder on its stdin. One process timestamps the
picture and the sound, which is what keeps them together: recording them
separately and muxing afterwards means two clocks and a guess at the distance
between them, and the guess plays every clip's sound early.

## Measured

One run on an RTX 3070, capturing a 2560x1440 display at 60 fps, 12 Mbps:

| Stage | Time |
|---|---|
| Cut (snapshot segments, concat, remux, poster frame, sha256) | 1.09 s |
| Hotkey to a clip kept on disk | 1.10 s |

A 16 second clip came out at 23.2 MB with both streams present, video 16.000 s
against audio 16.000 s.

Two caveats. Upload time is not included, because a clip is kept locally until
someone asks for a link, and the figure that matters then is the uplink rather
than anything this code does. And the cut is dominated by the work around the
remux, copying ~50 MB of segments and hashing the result, rather than by the
remux itself.

## Running it

Requires Windows, an NVENC-capable NVIDIA GPU, and Python 3.12 to run from
source. ffmpeg and cloudflared are bundled into the packaged build.

```
pip install -r requirements.txt
cd frontend && npm install && npm run build
python desktop.py
```

`desktop.py --verbose` adds access logs and devtools, `--no-capture` skips
recording, `--no-tray` makes closing the window quit.

| Part | Command |
|---|---|
| Desktop app | `python desktop.py` |
| API and share pages | `python -m uvicorn server.main:app --port 8000` |
| Capture daemon | `python -m capture.daemon` (`--test` clips once, `--probe` lists displays) |
| Frontend, dev | `cd frontend && npm run dev` |
| Tests | `python -m pytest` |

Building the executable: `python -m PyInstaller --clean --noconfirm arete.spec`.

## Setup

Run it. It asks three things and then records:

- **Where clips live.** This PC by default, which needs nothing installed: a
  SQLite file and a folder, both under `%APPDATA%\Arete`. The alternative is
  sending them to someone else's Arete, which needs their address and an
  invite code.
- **What to record.** A display, numbered the way Windows numbers it.
- **How much.** Clip length and the hotkey.

There is no key to obtain. A hosting install creates its own account on first
start and prints an address and invite code so another machine can join it.

## Settings

Reachable from the header of the library or from the tray icon.

- **Recording**: source, clip length, hotkey, quality, and an audio timing nudge
  for hardware that needs one. Saving restarts the capture.
- **Behaviour**: sound on capture, notifications, whether the X button quits or
  hides to the tray, clipboard behaviour, delete confirmation, and when to warn
  about clips piling up unshared.
- **This install**: where clips live, which device the sound comes from, the
  folder, and the address and invite another machine needs.

`config.env` is what the app needs to run, and setup writes it.
`preferences.json` is how it should behave; it is read at use, so a change
applies immediately.

## Capture and publish are separate

Pressing the hotkey keeps the moment. It does not upload anything.

A held clip lives as a file on the machine that recorded it and the server has
never heard of it. The library shows it with **No link yet**, plays it so you
can decide whether it was worth keeping, and uploads it only when you press
**Generate link**. Clips you never share never leave the machine.

## Sharing

The app opens a Cloudflare quick tunnel so links work off this machine, and
closes it on quit. `MANAGE_TUNNEL=false` turns that off, and links then only
resolve locally.

A quick tunnel gets a **new hostname every launch**, so links from a previous
session stop working. That is the one thing to know before sending anyone a
link you expect to last. A named tunnel on a domain you own is the fix, and it
needs a Cloudflare account rather than any code here.

Clip files are served from the same host, so a link carries both the page and
the video. Anyone with the link can watch; listing the library needs a key.

## Identity

Every client carries an API key in `X-API-Key`, and only its hash is stored.
`owner_id` on a clip comes from the key and the library query filters by it, so
libraries are separate by default.

Public on purpose: `/c/<slug>`, the rendition files, and `/healthz`. Everything
else needs a key. Acting on another user's clip returns 404 rather than 403,
because 403 confirms the clip exists.

## Layout

| Path | What it is |
|---|---|
| `desktop.py` | The app: API, capture, tray and window in one process |
| `branding.py` | The name, colours and mark. One source for every surface |
| `capture/` | `ringbuffer.py` -> `cutter.py` -> `uploader.py`, plus `loopback.py` for sound |
| `server/` | FastAPI. `main.py` routes, `models.py` schema, `storage.py` backends |
| `frontend/` | React and Vite clip library, served at `/app` |
| `ui/` | First-run setup and settings, plain HTML in the app window |
| `tools/` | Icon and launcher builders, user administration |
| `docs/design/` | The system design this implements |

## Tests

`python -m pytest` runs 139 tests covering the clip lifecycle, upload signature
enforcement, ring buffer segment selection, the settings bridge, single
instance, child process cleanup, and the audio pipe.

Capture itself is not covered: it needs a GPU and a live display.

This is a small application built to work reliably for one person, not a small
version of a large system. Where the design document behind it calls for dedup,
storage tiering, retention policies, event pipelines or multi-region, the
answer here is that those solve problems this does not have.
