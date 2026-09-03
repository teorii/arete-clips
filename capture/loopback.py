"""Desktop audio from whatever Windows is actually playing to.

ffmpeg's dshow input can only see devices that present themselves as capture
hardware. On this machine that is two microphones and a Stereo Mix belonging to
the Realtek chip, while sound goes out of the monitor over DisplayPort, so
recording Stereo Mix produced a silent track from a device nobody uses.

WASAPI can open any output device in loopback mode and follows the default one.
ffmpeg cannot do that, but it can read raw samples from a pipe, so this opens
the device and hands the samples straight to the encoder that is already
recording the screen.

That is the whole point of the pipe. Keeping sound in a buffer of its own and
muxing it onto the finished clip meant two recordings and two clocks, and
lining them up afterwards needed a guess at how far behind the encoder was
running. The guess was wrong, and every clip played its sound early. ffmpeg
timestamps both inputs as they arrive, on one clock, and the question does not
come up.
"""

from __future__ import annotations

import queue
import threading
import time

from problems import warn

# 16-bit stereo is what the encoder is told to expect on the pipe.
_SAMPLE_BYTES = 2

class Loopback:
    """The default playback device, piped into the encoder."""

    def __init__(self) -> None:
        self.rate = 48000
        self.channels = 2
        self.device: str | None = None

        self._audio = None
        self._stream = None
        self._sink = None
        self._writer: threading.Thread | None = None
        # Bounded on purpose. If the encoder ever stops reading, sound is
        # dropped rather than backing up until the capture stalls: a clip that
        # loses a moment of audio beats one that stops recording.
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=64)

    @property
    def available(self) -> bool:
        return self.device is not None

    @property
    def running(self) -> bool:
        return self._stream is not None

    def open(self) -> bool:
        """Find the default playback device. False if there is nothing to record.

        Separate from recording because the encoder has to be told the sample
        rate and channel count before it starts, and only the device knows them.
        """
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            warn("desktop audio", exc, "clips will be silent")
            return False

        try:
            self._audio = pyaudio.PyAudio()
            device = self._audio.get_default_wasapi_loopback()
            self.rate = int(device["defaultSampleRate"])
            self.channels = min(int(device["maxInputChannels"]), 2)
            self.device = str(device["name"])
            self._index = int(device["index"])
        except Exception as exc:  # noqa: BLE001
            warn("desktop audio", exc, "clips will be silent")
            self.close()
            return False
        return True

    def begin(self, sink) -> bool:
        """Start writing samples into `sink`, the encoder's stdin."""
        if not self.available:
            return False

        try:
            import pyaudiowpatch as pyaudio

            self._sink = sink

            def collect(data, _count, _info, _flags):
                try:
                    self._queue.put_nowait(data)
                except queue.Full:
                    pass  # see the queue's own note
                return (None, pyaudio.paContinue)

            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=self._index,
                frames_per_buffer=1024,
                stream_callback=collect,
            )
            self._writer = threading.Thread(
                target=self._pump, name="audio-pipe", daemon=True
            )
            self._writer.start()
        except Exception as exc:  # noqa: BLE001
            warn("desktop audio", exc, "clips will be silent")
            self.close()
            return False
        return True

    def _pump(self) -> None:
        """Move samples from the device to the encoder, at the rate of the clock.

        On its own thread because a write to a pipe can block, and blocking
        inside the audio callback is how a capture starts stuttering.

        The pipe is kept flowing whether or not anything is playing. Windows
        stops delivering buffers when the output goes idle, and ffmpeg will not
        finish a segment until every input has advanced, so a quiet moment
        stopped the recording outright: no video was written either, the
        watchdog restarted the encoder, and the buffer history went with it.
        That is what the stall and resume notifications were.

        Silence covers the gap, sized by how far behind the clock the stream
        has fallen. ffmpeg times raw samples by counting them, so this is also
        what keeps sound level with the picture across a quiet stretch instead
        of sliding forward by its length.
        """
        frame = self.channels * _SAMPLE_BYTES
        quiet = bytes(frame)
        started = time.monotonic()
        written = 0  # frames

        while True:
            try:
                block = self._queue.get(timeout=0.05)
            except queue.Empty:
                block = b""
            if block is None or self._sink is None:
                return

            try:
                if block:
                    self._sink.write(block)
                    written += len(block) // frame

                owed = int((time.monotonic() - started) * self.rate) - written
                # A twentieth of a second of slack, so ordinary jitter between
                # the device and the clock is not padded over.
                if owed > self.rate // 20:
                    self._sink.write(quiet * owed)
                    written += owed
            except (OSError, ValueError):
                # The encoder is gone or restarting. The watchdog owns that;
                # this thread just stops feeding a pipe nobody reads.
                return

    def close(self) -> None:
        stream, audio = self._stream, self._audio
        self._stream = self._audio = None
        self._queue.put(None)
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
            # PyAudio is terminated, not closed: close() belongs to a stream
            # and wants one as an argument.
            if audio is not None:
                audio.terminate()
        except Exception as exc:  # noqa: BLE001
            warn("desktop audio", exc, "a sound device was left open")
        self._sink = None
