# PyInstaller spec for the Arete client.
#
# One file, no prerequisites on the target machine: ffmpeg is bundled, so the
# app does not depend on whatever build happens to be on someone's PATH, and
# there is nothing to install first.
#
#     .venv\Scripts\python -m PyInstaller arete.spec --noconfirm
#
# ffmpeg is taken from the *shared* build on purpose. The static build embeds
# every codec into each binary, which is 212 MB per tool; shared is small
# executables beside one set of DLLs that both use.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PROJECT = Path(os.path.abspath(SPECPATH))


def ffmpeg_payload():
    """Bundle ffmpeg, ffprobe and the DLLs they need, flat beside the app."""
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
    ]
    for root in roots:
        if not root.exists():
            continue
        for candidate in root.rglob("ffmpeg.exe"):
            folder = candidate.parent
            if not (folder / "ffprobe.exe").exists():
                continue
            shared = list(folder.glob("*.dll"))
            # Prefer a shared build: it has DLLs beside the executables.
            if shared:
                return [(str(p), ".") for p in [candidate, folder / "ffprobe.exe", *shared]]
    raise SystemExit(
        "No ffmpeg found. Install the shared build:\n"
        "  winget install --id Gyan.FFmpeg.Shared -e"
    )


def cloudflared_payload():
    """Bundle cloudflared, so public links need nothing installed.

    Optional: without it the app still runs and clips still work, they are just
    only reachable from this machine.
    """
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "cloudflared" / "cloudflared.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [(str(candidate), ".")]
    print("arete.spec: cloudflared not found, public links will need it installed")
    return []


datas = ffmpeg_payload() + cloudflared_payload()
binaries = []
# server.main is reached through a lazy import, and its submodules through
# SQLAlchemy and FastAPI machinery, so name the package explicitly.
hiddenimports = ["clr", "server", "server.main", "server.models", "server.storage"]

# The built web app, so the exe can also act as host without a checkout.
dist = PROJECT / "frontend" / "dist"
if dist.is_dir():
    datas.append((str(dist), "frontend/dist"))

# The first-run setup page, loaded from disk by the setup window.
setup_ui = PROJECT / "ui"
if setup_ui.is_dir():
    datas.append((str(setup_ui), "ui"))

icon = PROJECT / "assets" / "arete.ico"
if icon.exists():
    datas.append((str(icon), "assets"))

# pywebview loads its platform backend and injected JS at runtime, and pystray
# picks a backend the same way, so neither is visible to static analysis.
for package in ("webview", "pystray", "PIL"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden


a = Analysis(
    ["desktop.py"],
    pathex=[str(PROJECT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "numpy", "boto3", "botocore", "psycopg"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# One file re-extracts the whole bundle to temp on every launch, which with
# ffmpeg inside is a couple of hundred megabytes each time. Set ARETE_ONEDIR=1
# to build the folder form instead, which starts immediately.
ONEDIR = bool(os.environ.get("ARETE_ONEDIR"))

common = dict(
    name="Arete",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon) if icon.exists() else None,
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Arete")
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common
    )
