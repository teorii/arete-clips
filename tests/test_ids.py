import time
from datetime import datetime, timezone

from server.ids import public_slug, uuid7


def test_uuid7_has_version_and_variant_bits():
    value = uuid7()
    assert value.version == 7
    assert (value.bytes[8] & 0xC0) == 0x80  # RFC 4122 variant


def test_uuid7_encodes_current_time():
    """The first 48 bits are a unix ms timestamp. That is what makes these sort
    by creation time, which is the whole reason for choosing v7."""
    before = datetime.now(timezone.utc).timestamp() * 1000
    value = uuid7()
    after = datetime.now(timezone.utc).timestamp() * 1000

    encoded_ms = int.from_bytes(value.bytes[:6], "big")
    assert before - 1000 <= encoded_ms <= after + 1000


def test_uuid7_values_sort_in_creation_order():
    """Ordering holds at millisecond granularity.

    v7 puts a 48-bit millisecond timestamp in the high bits and fills the rest
    with randomness, so two ids minted inside the same millisecond have no
    defined order relative to each other. Millisecond ordering is all the index
    locality argument needs, so that is what this asserts.
    """
    values = []
    for _ in range(6):
        values.append(uuid7())
        time.sleep(0.002)
    assert [str(v) for v in values] == sorted(str(v) for v in values)


def test_uuid7_timestamps_never_go_backwards():
    values = [uuid7() for _ in range(200)]
    stamps = [int.from_bytes(v.bytes[:6], "big") for v in values]
    assert stamps == sorted(stamps)


def test_public_slug_shape_and_uniqueness():
    slugs = {public_slug() for _ in range(500)}
    assert len(slugs) == 500, "slug collision in 500 draws"
    assert all(len(s) == 10 for s in slugs)
    assert all(s.isalnum() for s in slugs)
