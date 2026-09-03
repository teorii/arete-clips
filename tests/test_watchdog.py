"""Capture watchdog.

The app's worst failure is silent: ffmpeg dies from a driver reset or a display
change, the window carries on looking fine, and you find out by pressing F9
after the moment you wanted. These cover the decision logic; that a real ffmpeg
can be killed and restarted is verified separately against the actual process.
"""

import threading

import pytest

from desktop import CaptureService


class FakeRing:
    def __init__(self, alive: bool = True, revives: bool = True):
        self.alive = alive
        self.revives = revives
        self.starts = 0

    def is_running(self) -> bool:
        return self.alive

    def start(self) -> None:
        self.starts += 1
        self.alive = self.revives

    def stop(self) -> None:
        self.alive = False


class FakeDaemon:
    def __init__(self, ring: FakeRing):
        self.ring = ring


def run_watchdog(service: CaptureService, for_seconds: float = 0.5) -> None:
    thread = threading.Thread(target=service._watch, daemon=True)
    thread.start()
    thread.join(timeout=for_seconds)
    service._stopping.set()
    thread.join(timeout=2.0)


def make_service(ring: FakeRing) -> tuple[CaptureService, list]:
    events: list[tuple[bool, str]] = []
    service = CaptureService(on_health=lambda ok, msg: events.append((ok, msg)))
    service.poll_seconds = 0.01
    service.settle_seconds = 0.01
    service.daemon = FakeDaemon(ring)
    return service, events


def test_healthy_buffer_is_left_alone():
    ring = FakeRing(alive=True)
    service, events = make_service(ring)
    run_watchdog(service, 0.2)

    assert ring.starts == 0
    assert events == []


def test_dead_buffer_is_restarted_and_reported():
    ring = FakeRing(alive=False, revives=True)
    service, events = make_service(ring)
    run_watchdog(service, 0.3)

    assert ring.starts >= 1
    assert any(not ok for ok, _ in events), "never reported the stop"
    assert any(ok for ok, _ in events), "never reported the recovery"


def test_recovery_message_admits_the_buffer_was_lost():
    """Restarting clears the ring, so the history is gone. Say so rather than
    implying the last 60 seconds are still there to clip."""
    ring = FakeRing(alive=False, revives=True)
    service, events = make_service(ring)
    run_watchdog(service, 0.3)

    recovered = [msg for ok, msg in events if ok]
    assert recovered, "no recovery message"
    assert "lost" in recovered[0].lower()


def test_gives_up_after_repeated_failures_and_says_so():
    ring = FakeRing(alive=False, revives=False)
    service, events = make_service(ring)
    run_watchdog(service, 1.0)

    assert ring.starts == 3, f"expected 3 attempts, got {ring.starts}"
    assert service.error is not None
    assert not events[-1][0]
    assert "will not restart" in events[-1][1].lower()


def test_shutdown_stops_the_watchdog_rather_than_restarting():
    """stop() must not look like a crash, or teardown would race the watchdog
    into relaunching ffmpeg on the way out."""
    ring = FakeRing(alive=True)
    service, events = make_service(ring)

    thread = threading.Thread(target=service._watch, daemon=True)
    thread.start()
    service.stop()
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "watchdog ignored the stop signal"
    assert ring.starts == 0
