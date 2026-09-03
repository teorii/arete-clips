"""Ring buffer segment selection.

Pure file-selection logic, so it runs without a GPU or a capture process. This
is the arithmetic that decides which seconds end up in the clip, and an
off-by-one here silently truncates the moment the player pressed the key for.
"""

import os

from capture.config import CaptureSettings
from capture.ringbuffer import RingBuffer


def make_settings(tmp_path, **overrides) -> CaptureSettings:
    params = {
        "ring_buffer_dir": tmp_path / "ringbuf",
        "clip_seconds": 30,
        "buffer_seconds": 60,
        "segment_seconds": 2,
    }
    params.update(overrides)
    return CaptureSettings(**params)


def write_segments(directory, count: int, size: int = 1024):
    """Write segments with increasing mtimes, oldest first.

    Filenames are recycled by -segment_wrap, so chronological order comes from
    mtime, not from the name. Names here are deliberately shuffled relative to
    time to prove the code does not lean on them.
    """
    directory.mkdir(parents=True, exist_ok=True)
    created = []
    for index in range(count):
        # Reverse the naming so name order contradicts time order.
        path = directory / f"seg{count - index - 1:03d}.ts"
        path.write_bytes(b"\x47" * size)
        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
        created.append(path)
    return created


def test_segment_maths_matches_configuration(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.segments_per_clip == 15  # 30s clip / 2s segments
    assert settings.segment_count == 30  # 60s window / 2s segments


def test_segment_count_never_collapses_to_a_useless_ring(tmp_path):
    settings = make_settings(tmp_path, buffer_seconds=2, segment_seconds=2)
    assert settings.segment_count >= 4


def test_snapshot_takes_the_newest_segments_in_time_order(tmp_path):
    settings = make_settings(tmp_path)
    ring = RingBuffer(settings)
    created = write_segments(ring.dir, 30)

    staged = ring.snapshot(tmp_path / "staging")

    # 15 segments of clip, plus the one still being written.
    assert len(staged) == settings.segments_per_clip + 1
    # Staged parts are renamed sequentially, and must be in playback order.
    assert [p.name for p in staged] == [f"part{i:03d}.ts" for i in range(len(staged))]
    # The newest source segment must be included: it holds the payoff moment.
    # Trimmed to whole packets on the way out: the newest segment is copied
    # while ffmpeg is still writing it, and half a packet is what a decoder
    # reports as a damaged bitstream.
    from capture.ringbuffer import _TS_PACKET

    source = created[-1].read_bytes()
    keep = len(source) - len(source) % _TS_PACKET
    assert staged[-1].read_bytes() == source[:keep]


def test_snapshot_copes_with_a_partially_filled_ring(tmp_path):
    """Right after startup there are fewer segments than a full clip needs."""
    settings = make_settings(tmp_path)
    ring = RingBuffer(settings)
    write_segments(ring.dir, 3)

    staged = ring.snapshot(tmp_path / "staging")
    assert len(staged) == 3


def test_snapshot_of_an_empty_ring_returns_nothing(tmp_path):
    ring = RingBuffer(make_settings(tmp_path))
    ring.dir.mkdir(parents=True, exist_ok=True)
    assert ring.snapshot(tmp_path / "staging") == []


def test_snapshot_skips_zero_length_segments(tmp_path):
    """A segment ffmpeg has opened but not yet written would produce an empty
    input file, which makes the concat demuxer fail."""
    settings = make_settings(tmp_path)
    ring = RingBuffer(settings)
    write_segments(ring.dir, 5)
    empty = ring.dir / "seg099.ts"
    empty.write_bytes(b"")
    os.utime(empty, (1_700_000_099, 1_700_000_099))

    staged = ring.snapshot(tmp_path / "staging")
    assert all(p.stat().st_size > 0 for p in staged)
    assert len(staged) == 5


def test_a_staged_segment_is_cut_back_to_whole_packets(tmp_path):
    """The newest segment is copied while ffmpeg is still writing it, so it
    ends part-way through a packet. A decoder meeting half a packet reports a
    damaged bitstream, which is visible as corruption during playback."""
    from capture.ringbuffer import _TS_PACKET, _trim_to_whole_packets

    torn = tmp_path / "part000.ts"
    torn.write_bytes(b"x" * (_TS_PACKET * 3 + 57))

    assert _trim_to_whole_packets(torn) == _TS_PACKET * 3
    assert torn.stat().st_size % _TS_PACKET == 0


def test_a_whole_segment_is_left_alone(tmp_path):
    from capture.ringbuffer import _TS_PACKET, _trim_to_whole_packets

    intact = tmp_path / "part001.ts"
    intact.write_bytes(b"y" * (_TS_PACKET * 4))

    assert _trim_to_whole_packets(intact) == _TS_PACKET * 4
    assert intact.stat().st_size == _TS_PACKET * 4


def test_a_fragment_shorter_than_one_packet_is_dropped(tmp_path):
    from capture.ringbuffer import _trim_to_whole_packets

    scrap = tmp_path / "part002.ts"
    scrap.write_bytes(b"z" * 40)

    assert _trim_to_whole_packets(scrap) == 0
