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
    def __init__(self, alive: bool = True, revives: bool = True, stalled: bool = False):
        self.alive = alive
        self.revives = revives
        self.stalled = stalled
        self.starts = 0
        self.stops = 0

    def is_running(self) -> bool:
        return self.alive

    def is_stalled(self, _tolerance: float) -> bool:
        return self.stalled

    def start(self) -> None:
        self.starts += 1
        self.alive = self.revives
        self.stalled = False

    def stop(self) -> None:
        self.stops += 1
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


def test_a_wedged_encoder_is_treated_as_dead():
    """The case a liveness check misses: ffmpeg still running, producing
    nothing. Left alone the app looks healthy until the buffer comes up empty."""
    ring = FakeRing(alive=True, stalled=True)
    service, events = make_service(ring)
    run_watchdog(service, 0.3)

    assert ring.starts >= 1, "a stalled buffer was never restarted"
    assert any("stalled" in msg.lower() for _, msg in events)


def test_restarting_a_wedged_encoder_kills_it_first():
    """start() declines to act while the process is alive, so without the stop
    the restart would be a no-op and capture would stay dead."""
    ring = FakeRing(alive=True, stalled=True)
    service, _ = make_service(ring)
    run_watchdog(service, 0.3)

    assert ring.stops >= 1, "wedged process was never killed before restart"


def test_a_restarted_capture_rereads_the_config_first(monkeypatch):
    """Settings are cached for the life of the process. A restart that built
    the capture from the values it started with looked exactly like a source
    change that had not been written: the config said display 2, the encoder
    carried on with display 0."""
    import desktop

    order: list[str] = []

    def reread():
        order.append("reread")

    class Boom:
        def __init__(self):
            order.append("daemon")
            raise RuntimeError("far enough")

    monkeypatch.setattr(desktop, "capture_config", reread)
    monkeypatch.setattr(desktop, "Daemon", Boom)

    service = CaptureService()
    service._run()

    assert order == ["reread", "daemon"], "the daemon read settings nobody refreshed"
    assert "far enough" in (service.error or "")


def test_the_config_is_reread_from_disk_not_from_the_cache(tmp_path, monkeypatch):
    import desktop
    from capture.config import CaptureSettings, get_capture_settings

    monkeypatch.delenv("DDAGRAB_OUTPUT_IDX", raising=False)
    config = tmp_path / "config.env"
    config.write_text("DDAGRAB_OUTPUT_IDX=0\n", encoding="utf-8")
    monkeypatch.setattr(desktop, "config_file", lambda: config)
    original = CaptureSettings.model_config["env_file"]
    try:
        assert desktop.capture_config().ddagrab_output_idx == 0

        config.write_text("DDAGRAB_OUTPUT_IDX=2\n", encoding="utf-8")
        assert desktop.capture_config().ddagrab_output_idx == 2
    finally:
        CaptureSettings.model_config["env_file"] = original
        get_capture_settings.cache_clear()
