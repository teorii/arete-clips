from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CaptureSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_base_url: str = "http://localhost:8000"

    ring_buffer_dir: Path = Path("./ringbuf")
    clip_seconds: int = 30
    buffer_seconds: int = 60
    segment_seconds: int = 2

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
