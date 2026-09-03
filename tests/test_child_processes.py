"""Children do not outlive the app.

A force-killed Arete used to leave ffmpeg encoding on the GPU and holding the
ring buffer, so the next launch found its own segments locked, reported capture
as unavailable, and the hotkey did nothing.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PARENT = """
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import child_processes
assert child_processes.die_with_us()
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"],
    stdin=subprocess.DEVNULL,
)
print(child.pid, flush=True)
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x102
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return k.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        k.CloseHandle(handle)


def test_a_child_does_not_survive_a_killed_parent():
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT, str(ROOT)],
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    try:
        child_pid = int(parent.stdout.readline().strip())
        assert _alive(child_pid), "the child should be running before the kill"

        parent.kill()
        parent.wait(timeout=10)

        deadline = time.time() + 10
        while time.time() < deadline and _alive(child_pid):
            time.sleep(0.2)
        assert not _alive(child_pid), "the child outlived the app it belonged to"
    finally:
        parent.kill()
