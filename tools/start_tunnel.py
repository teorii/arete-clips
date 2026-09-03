"""Open the tunnels without running the app.

The app does this itself on start, so this is for the case where you want the
host published while running the API from a terminal.

Quick tunnels get a new hostname every run, so links made in one session stop
working in the next. Fine for a demo, not for anything else: a named tunnel on
your own domain is the fix when links need to last.
"""

from __future__ import annotations

import sys
import time

from paths import config_file
from services import Tunnels


def main() -> int:
    tunnels = Tunnels()
    print("Opening tunnels. This takes a few seconds.")
    addresses = tunnels.start()
    if not addresses:
        return 1

    api_url, storage_url = addresses
    print()
    print(f"  app and share pages : {api_url}")
    print(f"  clip files          : {storage_url}/clips")
    print()
    print(f"Written to {config_file()}.")
    print("Restart the API so it picks these up.")
    print("Leave this window open. Closing it kills both tunnels.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        tunnels.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
