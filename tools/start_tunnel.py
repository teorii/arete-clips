"""Expose the host publicly with Cloudflare quick tunnels.

    python -m tools.start_tunnel

Two tunnels are needed, not one. The API serves pages and metadata on 8000, but
the clips themselves are served straight from object storage on 9000, which is
the whole point of the upload design. Exposing only the API would produce share
pages whose video URLs still say 127.0.0.1 and play for nobody.

So this starts a tunnel for each, writes the assigned hostnames into the config
as PUBLIC_BASE_URL and S3_PUBLIC_BASE_URL, and holds them open.

Quick tunnels get a new random hostname every run, so links made in one session
stop working in the next. That is fine for a demo and not fine for anything
else: a named tunnel on your own domain is the fix when you need links to last.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from paths import config_file

URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found

    # Installed by MSI it lands in Program Files rather than the winget package
    # store, and neither location is on PATH for an already-open shell.
    import os

    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "cloudflared",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "cloudflared",
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages",
    ]
    for directory in candidates:
        if not directory.exists():
            continue
        direct = directory / "cloudflared.exe"
        if direct.exists():
            return str(direct)
        for nested in directory.rglob("cloudflared*.exe"):
            return str(nested)
    return None


class Tunnel:
    def __init__(self, binary: str, port: int, label: str):
        self.label = label
        self.url: str | None = None
        self._ready = threading.Event()
        self.proc = subprocess.Popen(
            [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=_NO_WINDOW,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            if self.url is None:
                match = URL_PATTERN.search(line)
                if match:
                    self.url = match.group(0)
                    self._ready.set()

    def wait(self, timeout: float = 45.0) -> str | None:
        self._ready.wait(timeout)
        return self.url

    def stop(self) -> None:
        self.proc.terminate()


def update_config(**values: str) -> None:
    path = config_file()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", text, flags=re.M):
            text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    binary = find_cloudflared()
    if not binary:
        print("cloudflared not found. Install it with:")
        print("  winget install --id Cloudflare.cloudflared -e")
        return 1

    print("Opening tunnels. This takes a few seconds.\n")
    api = Tunnel(binary, 8000, "api")
    storage = Tunnel(binary, 9000, "storage")

    api_url = api.wait()
    storage_url = storage.wait()
    if not api_url or not storage_url:
        print("A tunnel did not come up. Is cloudflared able to reach the internet?")
        api.stop()
        storage.stop()
        return 1

    # The bucket is addressed by path, so playback lives one level down.
    update_config(
        PUBLIC_BASE_URL=api_url,
        S3_PUBLIC_BASE_URL=f"{storage_url}/clips",
        # Uploads are signed for this host. Without it a client on another
        # machine is handed an upload URL pointing at its own localhost.
        S3_PUBLIC_ENDPOINT_URL=storage_url,
    )

    print(f"  app and share pages : {api_url}")
    print(f"  clip files          : {storage_url}/clips")
    print(f"\nWritten to {config_file()}.")
    print("Restart Arete so it picks these up, then share links from the app.")
    print("\nLeave this window open. Closing it kills both tunnels and every")
    print("link stops working. Quick tunnel hostnames change on every run.")

    try:
        while api.proc.poll() is None and storage.proc.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        api.stop()
        storage.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
