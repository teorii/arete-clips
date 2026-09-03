"""Publishing the host, so it is not a window to remember.

Only the host does this. A machine that sends its clips somewhere else talks to
an API someone else is running and starts nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from paths import bundle_dir, config_file

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def find_binary(name: str, extra: list[Path] | None = None) -> str | None:
    """Bundled copy first, then PATH, then where installers put things.

    Bundled wins so a packaged app does not depend on whatever version happens
    to be on a stranger's PATH, or on anything being there at all.
    """
    bundled = bundle_dir() / f"{name}.exe"
    if bundled.exists():
        return str(bundled)

    found = shutil.which(name)
    if found:
        return found
    roots = list(extra or [])
    roots.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages")
    for root in roots:
        if not root.exists():
            continue
        direct = root / f"{name}.exe"
        if direct.exists():
            return str(direct)
        for nested in root.rglob(f"{name}*.exe"):
            return str(nested)
    return None


def cloudflared() -> str | None:
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return find_binary("cloudflared", [Path(x86) / "cloudflared", Path(program_files) / "cloudflared"])


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


class Tunnel:
    """One cloudflared quick tunnel, holding the hostname it was given."""

    def __init__(self, binary: str, port: int):
        self.url: str | None = None
        self._ready = threading.Event()
        self.proc = subprocess.Popen(
            [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1,
            creationflags=_NO_WINDOW,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            if self.url is None:
                match = _TUNNEL_URL.search(line)
                if match:
                    self.url = match.group(0)
                    self._ready.set()

    def wait(self, timeout: float = 45.0) -> str | None:
        self._ready.wait(timeout)
        return self.url

    def resolvable(self, timeout: float = 30.0) -> bool:
        """Whether the hostname actually resolves yet.

        cloudflared prints the address before DNS has caught up, so a client
        handed it immediately gets a lookup failure. Waiting here is the
        difference between announcing a working address and a promise.
        """
        if not self.url:
            return False
        host = self.url.split("/")[2]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                socket.getaddrinfo(host, 443)
                return True
            except socket.gaierror:
                time.sleep(1.0)
        return False

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


class Tunnels:
    """A public address for this install.

    One, not two. Clips used to come from a storage server on its own port, so
    exposing only the API produced share pages whose video URLs still pointed at
    127.0.0.1 and played for nobody. That server is gone: the app serves the
    pages and the files, and a second tunnel was one more thing that had to come
    up before anyone could be given a link.
    """

    def __init__(self, api_port: int = 8000):
        self.api_port = api_port
        self.api: Tunnel | None = None

    def start(self) -> str | None:
        binary = cloudflared()
        if not binary:
            print("  cloudflared not found. Install it:")
            print("    winget install --id Cloudflare.cloudflared -e")
            return None

        self.api = Tunnel(binary, self.api_port)
        api_url = self.api.wait()
        if not api_url:
            print("  The tunnel did not come up.")
            self.stop()
            return None

        if not self.api.resolvable():
            print("  The tunnel opened but its address does not resolve yet.")
            self.stop()
            return None

        update_config(PUBLIC_BASE_URL=api_url)
        return api_url

    def stop(self) -> None:
        if self.api is not None:
            self.api.stop()
        self.api = self.storage = None
