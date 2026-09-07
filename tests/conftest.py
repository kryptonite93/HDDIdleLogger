from types import SimpleNamespace

import pytest

from app.collector import Collector
from app.config import Settings
from app.database import Database


def diskstats(reads=10, writes=20, sectors_read=100, sectors_written=200, name="sdb", discards=0, flushes=0):
    return f"8 16 {name} {reads} 0 {sectors_read} 0 {writes} 0 {sectors_written} 0 0 0 0 {discards} 0 0 0 {flushes} 0\n"


@pytest.fixture
def rig(tmp_path):
    config = SimpleNamespace(data_dir=tmp_path, diskstats_path=tmp_path/"diskstats",
                             sys_block_path=tmp_path/"sys", boot_id_path=tmp_path/"boot_id")
    config.diskstats_path.write_text(diskstats())
    config.boot_id_path.write_text("boot-one")
    db = Database(tmp_path/"profiler.sqlite3")
    collector = Collector(db, config, Settings())
    return config, db, collector


def poll(collector, second, **kwargs):
    collector.config.diskstats_path.write_text(diskstats(**kwargs))
    assert collector.sample(now=1_700_000_000+second, monotonic=second)
