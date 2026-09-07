from app.config import Settings
from app.diskstats import Counters, eligible, parse_diskstats
from conftest import diskstats


def test_modern_and_legacy_and_malformed():
    rows = parse_diskstats(diskstats(flushes=4, discards=3) + "malformed\n8 0 sdz x 0\n")
    assert rows["sdb"] == Counters(10, 100, 20, 200, 3, 0, 4)
    old = "8 0 sda 1 0 2 0 3 0 4 0 0 0 0"
    assert parse_diskstats(old)["sda"] == Counters(1, 2, 3, 4)
    assert not parse_diskstats(old.replace("sda 1", "sda -1"))


def test_filters(tmp_path):
    settings = Settings()
    assert eligible("sdaa", {}, settings, tmp_path)
    for name in ("sda1", "nvme0n1", "md1", "dm-0", "loop0", "sr0"):
        assert not eligible(name, {}, Settings(include_device_regex=".*"), tmp_path)
    assert not eligible("sda", {"rotational": 0}, settings, tmp_path)
    assert not eligible("sdb", {}, Settings(exclude_device_regex="sdb"), tmp_path)
