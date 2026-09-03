from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from paths import config_file, data_dir


class CaptureSettings(BaseSettings):
    # Resolved rather than hardcoded: packaged, the config lives in AppData,
    # because a one-file build unpacks to a temp directory that is wiped.
    model_config = SettingsConfigDict(env_file=str(config_file()), extra="ignore")

    api_base_url: str = "http://localhost:8000"
    # Issued by tools/add_user.py. Identifies which library a clip belongs to.
    arete_api_key: str = ""

    # Under the per-user data directory, not the working directory. Launched
    # from a shortcut the working directory is wherever Windows felt like, and
    # may not be writable at all.
    ring_buffer_dir: Path = data_dir() / "ringbuf"
    clip_seconds: int = 30
    buffer_seconds: int = 60
    segment_seconds: int = 2

    # "auto" finds a loopback device, "none" records silent video, anything
    # else is an exact dshow device name.
    audio_device: str = "auto"

    # ffmpeg timestamps the picture and the sound on one clock, so this starts
    # at nothing. It is here for hardware that still needs a nudge: positive
    # moves the sound later, which is the fix when it arrives early.
    audio_offset_ms: int = 0

    # Off by default: capturing and publishing are separate decisions. The
    # hotkey keeps the moment, and a link is asked for afterwards.
    auto_upload: bool = False

    capture_fps: int = 60
    capture_bitrate: str = "12M"
    # Which desktop output the Desktop Duplication API should grab.
    ddagrab_output_idx: int = 0

    # Virtual-key code for the clip hotkey. 0x78 is F9.
    hotkey_vk: int = 0x78

    @field_validator("hotkey_vk", mode="before")
    @classmethod
    def _parse_vk(cls, value: object) -> object:
        """Accept 0x78 as well as 120, since VK codes are documented in hex."""
        if isinstance(value, str):
            text = value.strip()
            if text.lower().startswith("0x"):
                return int(text, 16)
        return value

    @property
    def segment_count(self) -> int:
        """Ring size. Wraps automatically, so the window is self-limiting."""
        return max(4, self.buffer_seconds // self.segment_seconds)

    @property
    def segments_per_clip(self) -> int:
        return max(1, -(-self.clip_seconds // self.segment_seconds))


@lru_cache
def get_capture_settings() -> CaptureSettings:
    return CaptureSettings()
