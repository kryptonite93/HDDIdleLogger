import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_interval_seconds: int = Field(default=30, ge=10, le=3600)
    timeouts_minutes: list[float] = Field(default=[15, 30, 60, 120], min_length=1, max_length=20)
    cycling_warning_threshold: float = Field(default=4, gt=0, le=10000)
    recommendation_efficiency_threshold: float = Field(default=0.85, gt=0, le=1)
    include_device_regex: str = "^sd[a-z]+$"
    exclude_device_regex: str = ""
    timezone: str = "UTC"

    @field_validator("timeouts_minutes")
    @classmethod
    def timeouts(cls, values):
        if any(not 0 < value <= 10080 for value in values):
            raise ValueError("Timeouts must be finite numbers between 0 and 10080 minutes")
        return sorted(set(values))

    @field_validator("include_device_regex", "exclude_device_regex")
    @classmethod
    def regex(cls, value):
        if len(value) > 200:
            raise ValueError("Regex must be at most 200 characters")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"Invalid regex: {exc}") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use an IANA timezone such as America/Toronto") from exc
        return value

    @classmethod
    def from_env(cls):
        return cls(sample_interval_seconds=os.getenv("SAMPLE_INTERVAL_SECONDS", "30"),
                   include_device_regex=os.getenv("INCLUDE_DEVICE_REGEX", "^sd[a-z]+$"),
                   exclude_device_regex=os.getenv("EXCLUDE_DEVICE_REGEX", ""),
                   timezone=os.getenv("TZ", "UTC"))


class Config:
    def __init__(self):
        self.data_dir = Path(os.getenv("DATA_DIR", "/data"))
        self.diskstats_path = Path(os.getenv("DISKSTATS_PATH", "/host/proc/diskstats"))
        self.sys_block_path = Path(os.getenv("SYS_BLOCK_PATH", "/host/sys/block"))
        self.boot_id_path = Path(os.getenv("BOOT_ID_PATH", "/proc/sys/kernel/random/boot_id"))
