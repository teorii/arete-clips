"""Desktop sound, handed to the encoder rather than kept separately.

The device itself needs real hardware. What is covered here is the wiring: that
the encoder is told to expect samples on its stdin, that it times them against
the picture, and that a machine with no usable device still records.
"""

from __future__ import annotations

import queue

from capture.config import CaptureSettings
from capture.loopback import Loopback
from capture.ringbuffer import RingBuffer


def _device(rate: int = 48000, channels: int = 2) -> Loopback:
    sound = Loopback()
    sound.device = "Speakers [Loopback]"
    sound.rate, sound.channels = rate, channels
    return sound


def _ring(sound: Loopback | None, **settings) -> RingBuffer:
    ring = RingBuffer(CaptureSettings(**settings))
    ring.loopback = sound
    return ring


def test_the_encoder_is_told_to_read_samples_from_its_stdin():
    """One process, one clock. Keeping sound in a buffer of its own meant
    lining two recordings up afterwards, which needed a guess at how far behind
    the encoder was running, and the guess played every clip's sound early."""
    command = _ring(_device()).command()

    assert "pipe:0" in command
    assert command[command.index("-f", command.index("-thread_queue_size")) + 1] == "s16le"
    assert "0:v" in command and "1:a" in command
    assert "aac" in command


def test_the_pipe_matches_what_the_device_actually_produces():
    command = _ring(_device(rate=44100, channels=1)).command()

    assert command[command.index("-ar") + 1] == "44100"
    assert command[command.index("-ac") + 1] == "1"


def test_no_device_means_a_silent_clip_not_a_broken_one():
    command = _ring(None).command()

    assert "pipe:0" not in command
    assert "1:a" not in command
    # The picture is still recorded, which is the part nobody can do without.
    assert "h264_nvenc" in command


def test_the_timing_nudge_reaches_the_encoder():
    command = _ring(_device(), audio_offset_ms=250).command()

    assert command[command.index("-itsoffset") + 1] == "0.250"
    assert command.index("-itsoffset") < command.index("pipe:0")


def test_no_nudge_is_asked_for_by_default():
    assert "-itsoffset" not in _ring(_device()).command()


def test_sound_is_dropped_rather_than_stalling_the_capture():
    """If the encoder stops reading, the queue fills. Sound is lost from that
    moment, which is better than the capture backing up behind it."""
    sound = _device()
    sound._queue = queue.Queue(maxsize=2)

    for _ in range(5):
        try:
            sound._queue.put_nowait(b"\x00\x00")
        except queue.Full:
            pass

    assert sound._queue.qsize() == 2


def test_the_pipe_keeps_flowing_when_nothing_is_playing():
    """Windows stops delivering buffers when the output goes idle, and ffmpeg
    will not finish a segment until every input has advanced. A quiet moment
    therefore stopped the recording outright: no video either, the watchdog
    restarted the encoder, and the buffer history went with it. That was the
    stalled and resumed pair of notifications."""
    import threading
    import time

    sound = _device()
    written: list[int] = []

    class Sink:
        def write(self, data: bytes) -> None:
            written.append(len(data))

    sound._sink = Sink()
    pump = threading.Thread(target=sound._pump, daemon=True)
    pump.start()
    try:
        time.sleep(0.5)
    finally:
        sound._queue.put(None)
        pump.join(timeout=2)

    assert written, "the encoder was fed nothing while the device was quiet"
    # Roughly half a second of silence, paced by the clock rather than dumped.
    frames = sum(written) // (sound.channels * 2)
    assert 0.3 < frames / sound.rate < 0.8


def test_real_samples_are_not_padded_over():
    """Silence only covers what the device did not deliver: padding on top of
    real sound would push the track ahead of the picture."""
    import threading
    import time

    sound = _device()
    written: list[bytes] = []

    class Sink:
        def write(self, data: bytes) -> None:
            written.append(data)

    sound._sink = Sink()
    frame = sound.channels * 2
    # A tenth of a second of very loud samples.
    sound._queue.put(b"\x7f\x7f" * (sound.rate // 10 * sound.channels))

    pump = threading.Thread(target=sound._pump, daemon=True)
    pump.start()
    try:
        time.sleep(0.3)
    finally:
        sound._queue.put(None)
        pump.join(timeout=2)

    loud = sum(block.count(b"\x7f") for block in written)
    assert loud >= sound.rate // 10 * sound.channels, "real sound was dropped"
    total = sum(len(b) for b in written) // frame
    # The real tenth plus padding for the rest, not a tenth counted twice.
    assert 0.2 < total / sound.rate < 0.5
