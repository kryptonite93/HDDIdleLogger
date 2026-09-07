"""Read kernel counters only. No block device, SMART, or filesystem probing."""
import re
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class Counters:
    reads: int
    sectors_read: int
    writes: int
    sectors_written: int
    discards: int = 0
    sectors_discarded: int = 0
    flushes: int = 0

    def delta(self, previous):
        values = {k: v - asdict(previous)[k] for k, v in asdict(self).items()}
        return None if any(v < 0 for v in values.values()) else values


def parse_diskstats(text: str) -> dict[str, Counters]:
    devices = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 14 or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", fields[2]):
            continue
        try:
            int(fields[0]), int(fields[1])
            numbers = list(map(int, fields[3:]))
            if any(v < 0 for v in numbers):
                continue
            devices[fields[2]] = Counters(numbers[0], numbers[2], numbers[4], numbers[6],
                                         numbers[11] if len(numbers) >= 15 else 0,
                                         numbers[13] if len(numbers) >= 15 else 0,
                                         numbers[15] if len(numbers) >= 17 else 0)
        except ValueError:
            continue
    return devices


def metadata(root: Path, name: str) -> dict:
    def read(relative):
        try:
            return (root / name / relative).read_text().strip() or None
        except (OSError, UnicodeError):
            return None
    rotation = read("queue/rotational")
    return {"model": read("device/model"), "serial": read("device/serial"),
            "vendor": read("device/vendor"),
            "rotational": int(rotation) if rotation in ("0", "1") else None}


def eligible(name: str, info: dict, settings, root: Path) -> bool:
    # Never allow partition / virtual-device inclusion through a permissive regex.
    if not re.fullmatch(r"sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+", name):
        return False
    if (root / name / "partition").exists() or info.get("rotational") == 0:
        return False
    return bool(re.search(settings.include_device_regex, name)) and not (
        settings.exclude_device_regex and re.search(settings.exclude_device_regex, name))
