from pathlib import Path

from app.collector import Collector
from conftest import diskstats, poll


def test_unchanged_poll_has_no_event_and_one_checkpoint(rig):
    _, db, collector = rig
    for second in range(0, 301, 30):
        poll(collector, second)
    assert not db.rows("SELECT * FROM activity_events")
    assert not db.rows("SELECT * FROM idle_intervals")
    assert len(db.rows("SELECT * FROM observation_sessions")) == 1
    assert db.rows("SELECT last_observed_at-started_at AS duration FROM observation_sessions")[0]["duration"] == 300


def test_deltas_and_initial_censoring(rig):
    _, db, collector = rig
    poll(collector, 0)
    poll(collector, 30, reads=12, sectors_read=108, flushes=1)
    poll(collector, 60, reads=13, sectors_read=116, flushes=2)
    rows = db.rows("SELECT * FROM activity_events")
    assert rows[0]["reads_delta"] == 2
    assert rows[0]["bytes_read_delta"] == 8*512
    assert rows[0]["flushes_delta"] == 1
    assert [i["start_is_censored"] for i in db.rows("SELECT * FROM idle_intervals")] == [1, 0]


def test_reset_and_disappearance_do_not_invent_activity(rig):
    config, db, collector = rig
    poll(collector, 0)
    poll(collector, 30, reads=1)
    config.diskstats_path.write_text(diskstats(name="sdc"))
    collector.sample(now=1_700_000_060, monotonic=60)
    assert not db.rows("SELECT * FROM activity_events")
    assert {r["event_type"] for r in db.rows("SELECT * FROM collector_events")} >= {"counter_reset", "device_missing"}
    assert db.rows("SELECT present FROM disks WHERE device_name='sdb'")[0]["present"] == 0


def test_restart_ends_at_last_checkpoint(rig):
    config, db, collector = rig
    poll(collector, 0)
    poll(collector, 30, reads=11)
    poll(collector, 60, reads=11)
    restarted = Collector(db, config, collector.settings)
    poll(restarted, 7200, reads=11)
    sessions = db.rows("SELECT * FROM observation_sessions")
    assert len(sessions) == 2
    assert sessions[0]["ended_at"] == 1_700_000_060
    assert sessions[1]["last_activity_at"] is None
    assert len(db.rows("SELECT * FROM activity_events")) == 1


def test_source_failure_even_short_gap_breaks_continuity(rig):
    config, db, collector = rig
    poll(collector, 0)
    config.diskstats_path.write_text("not counters")
    assert not collector.sample(now=1_700_000_030, monotonic=30)
    poll(collector, 60)
    assert len(db.rows("SELECT * FROM observation_sessions")) == 2


def test_long_delay_and_clock_change_and_boot(rig):
    config, db, collector = rig
    poll(collector, 0)
    poll(collector, 300)
    assert collector.sample(now=1_700_005_330, monotonic=330)
    config.boot_id_path.write_text("boot-two")
    assert collector.sample(now=1_700_005_360, monotonic=360)
    kinds = {r["event_type"] for r in db.rows("SELECT * FROM collector_events")}
    assert kinds >= {"sampling_gap", "clock_change", "boot_change"}


def test_collection_reads_only_counters_and_metadata(rig, monkeypatch):
    config, _, collector = rig
    original = Path.read_text
    opened = []
    def read(path, *args, **kwargs):
        opened.append(path)
        assert path == config.diskstats_path or path == config.boot_id_path or config.sys_block_path in path.parents
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    collector.sample()
    assert config.diskstats_path in opened


def test_optional_metadata_can_be_missing_and_ssd_is_excluded(rig):
    config, db, collector = rig
    (config.sys_block_path/"sdb"/"queue").mkdir(parents=True)
    (config.sys_block_path/"sdb"/"queue"/"rotational").write_text("0")
    poll(collector, 0)
    assert db.rows("SELECT enabled,eligible FROM disks")[0] == {"enabled": 0, "eligible": 0}
    assert not db.rows("SELECT * FROM observation_sessions")
    (config.sys_block_path/"sdb"/"queue"/"rotational").unlink()
    poll(collector, 30)
    assert db.rows("SELECT rotational,eligible FROM disks")[0] == {"rotational": 0, "eligible": 0}
