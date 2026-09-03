"""Starting the things the host needs, so they are not windows to remember.

Only the host does any of this. A second machine talks to an API someone else
is running and starts nothing.

Postgres is deliberately not managed here: it is a Windows service and starting
it needs elevation, so the app checks it and says what to run rather than
pretending it can.
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

from paths import bundle_dir, config_file, data_dir

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


class StorageServer:
    """MinIO, started only if nothing is already serving on its port.

    Adopting a server someone else started matters: running the app while a
    terminal copy is up must not produce two processes fighting over the same
    data directory.
    """

    PORT = 9000

    def __init__(self, access_key: str, secret_key: str, directory: Path | None = None):
        self.access_key = access_key
        self.secret_key = secret_key
        self.directory = Path(directory or data_dir() / "storage")
        self.proc: subprocess.Popen | None = None
        self.adopted = False

    def start(self, timeout: float = 20.0) -> bool:
        if port_open(self.PORT):
            self.adopted = True
            return True

        binary = find_binary("minio")
        if not binary:
            print("  MinIO not found. Install it:  winget install --id MinIO.Server -e")
            return False
        if not (self.access_key and self.secret_key):
            print("  Storage credentials are not set, so MinIO cannot start.")
            return False

        self.directory.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [binary, "server", str(self.directory),
             "--address", f"127.0.0.1:{self.PORT}",
             "--console-address", "127.0.0.1:9001"],
            env={**os.environ,
                 "MINIO_ROOT_USER": self.access_key,
                 "MINIO_ROOT_PASSWORD": self.secret_key},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_open(self.PORT):
                return True
            if self.proc.poll() is not None:
                print("  MinIO exited while starting.")
                return False
            time.sleep(0.25)
        print(f"  MinIO did not open port {self.PORT} within {timeout:g}s.")
        return False

    def stop(self) -> None:
        # Never kill a server this process did not start.
        if self.adopted or self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None


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
    """Public addresses for the API and for the clips themselves.

    Two, not one. The API serves pages on 8000 but clips come straight from
    storage on 9000, so exposing only the API produces share pages whose video
    URLs still point at 127.0.0.1 and play for nobody.
    """

    def __init__(self, api_port: int = 8000, storage_port: int = 9000):
        self.api_port = api_port
        self.storage_port = storage_port
        self.api: Tunnel | None = None
        self.storage: Tunnel | None = None

    def start(self) -> tuple[str, str] | None:
        binary = cloudflared()
        if not binary:
            print("  cloudflared not found. Install it:")
            print("    winget install --id Cloudflare.cloudflared -e")
            return None

        self.api = Tunnel(binary, self.api_port)
        self.storage = Tunnel(binary, self.storage_port)
        api_url, storage_url = self.api.wait(), self.storage.wait()
        if not api_url or not storage_url:
            print("  A tunnel did not come up.")
            self.stop()
            return None

        if not (self.api.resolvable() and self.storage.resolvable()):
            print("  Tunnels opened but their addresses do not resolve yet.")
            self.stop()
            return None

        update_config(
            PUBLIC_BASE_URL=api_url,
            S3_PUBLIC_BASE_URL=f"{storage_url}/clips",
            # Uploads are signed for this host, so a client elsewhere has to be
            # given an address it can actually reach.
            S3_PUBLIC_ENDPOINT_URL=storage_url,
        )
        return api_url, storage_url

    def stop(self) -> None:
        for tunnel in (self.api, self.storage):
            if tunnel is not None:
                tunnel.stop()
        self.api = self.storage = None
