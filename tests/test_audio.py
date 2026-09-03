"""Desktop audio device discovery.

Parsing is kept separate from spawning ffmpeg so the interesting half can be
tested on a machine that has no loopback device, which is every machine until
someone installs one.
"""

from capture.audio import find_loopback_device, parse_devices, pick_loopback

# Real output from `ffmpeg -list_devices true -f dshow -i dummy`, trimmed.
FFMPEG_LISTING = r'''
[in#0 @ 000002c5] "HD Pro Webcam C920" (video)
[in#0 @ 000002c5]   Alternative name "@device_pnp_usb#vid_046d&pid_082d"
[in#0 @ 000002c5] "OBS Virtual Camera" (video)
[in#0 @ 000002c5]   Alternative name "@device_sw_{860BB310-5D01-11D0}"
[in#0 @ 000002c5] "Microphone (AT2020USB+)" (audio)
[in#0 @ 000002c5]   Alternative name "@device_cm_{33D9A762-90C8-11D0}"
[in#0 @ 000002c5] "virtual-audio-capturer" (audio)
[in#0 @ 000002c5]   Alternative name "@device_sw_{860BB310-5D01-11D0}"
'''


def test_parses_audio_devices_and_ignores_video():
    assert parse_devices(FFMPEG_LISTING, "audio") == [
        "Microphone (AT2020USB+)",
        "virtual-audio-capturer",
    ]


def test_alternative_name_lines_are_not_treated_as_devices():
    """Those lines carry a quoted string too, but it is not usable as an input."""
    for name in parse_devices(FFMPEG_LISTING, "audio"):
        assert not name.startswith("@device")


def test_parses_video_devices_separately():
    assert parse_devices(FFMPEG_LISTING, "video") == [
        "HD Pro Webcam C920",
        "OBS Virtual Camera",
    ]


def test_picks_the_loopback_device_over_a_microphone():
    devices = ["Microphone (AT2020USB+)", "virtual-audio-capturer"]
    assert pick_loopback(devices) == "virtual-audio-capturer"


def test_recognises_a_driver_provided_stereo_mix():
    assert pick_loopback(["Stereo Mix (Realtek(R) Audio)"]) == "Stereo Mix (Realtek(R) Audio)"


def test_a_machine_with_only_microphones_gets_no_audio():
    """Must return None rather than grabbing a mic. Recording the user's room
    instead of the game would be worse than silence."""
    assert pick_loopback(["Microphone (AT2020USB+)", "Headset Microphone"]) is None


def test_explicit_device_name_bypasses_detection():
    assert find_loopback_device("Stereo Mix") == "Stereo Mix"


def test_none_disables_audio_without_probing():
    assert find_loopback_device("none") is None
