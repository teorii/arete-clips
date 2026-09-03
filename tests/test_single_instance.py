"""One copy at a time.

The packaged app is built with console=False, so a second launch printing
"port already in use" printed it nowhere: double-clicking the icon looked like
nothing happened at all.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CLAIMER = """
import sys, time
sys.path.insert(0, sys.argv[1])
import single_instance
print("claimed" if single_instance.claim(sys.argv[3]) else "refused", flush=True)
time.sleep(float(sys.argv[2]))
"""


# Its own name, so the suite passes whether or not Arete is running.
NAME = f"arete-test-{uuid.uuid4().hex}"


def _claim(hold: float) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", CLAIMER, str(ROOT), str(hold), NAME],
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
    )


def test_a_second_copy_is_refused_while_the_first_holds_it():
    first = _claim(5)
    try:
        assert first.stdout.readline().strip() == "claimed"

        second = _claim(0)
        assert second.stdout.read().strip() == "refused"
        second.wait(timeout=10)
    finally:
        first.kill()
        first.wait(timeout=10)


def test_the_mutex_is_released_when_the_first_copy_exits():
    """Windows releases a named mutex however the process ends, so there is no
    stale lock to clear the way a lock file would leave one after a crash."""
    first = _claim(0)
    assert first.stdout.read().strip() == "claimed"
    first.wait(timeout=10)

    after = _claim(0)
    assert after.stdout.read().strip() == "claimed"
    after.wait(timeout=10)
