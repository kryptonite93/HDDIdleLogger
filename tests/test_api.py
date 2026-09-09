import csv
import io
import time

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import diskstats


def test_baseline_and_restart_do_not_claim_activity(rig):
    config, _, _ = rig
    for restart in range(2):
        with TestClient(create_app(config, start_collector=False)) as client:
            collector = client.app.state.collector
            # Use the real health check with a deterministic sample sequence.
            base = time.monotonic()
            def sample(second, reads):
                config.diskstats_path.write_text(diskstats(reads=reads))
                assert collector.sample(now=1700000000+restart*300+second, monotonic=base+second)

            sample(0, 10)
            baseline = client.get('/api/disks/sdb').json()
            assert baseline['state'] == 'Waiting for next reading'
            assert baseline['current_idle_is_censored'] is True
            sample(30, 10)
            assert client.get('/api/disks/sdb').json()['state'] == 'Idle'
            sample(60, 11)
            assert client.get('/api/disks/sdb').json()['state'] == 'Active recently'
            sample(90, 11)
            assert client.get('/api/disks/sdb').json()['state'] == 'Idle'
            collector.last_success_mono = time.monotonic()-100
            assert client.get('/api/disks/sdb').json()['state'] == 'Paused'


def test_pages_and_health_and_validation(rig):
    config, _, _ = rig
    with TestClient(create_app(config, start_collector=False)) as client:
        for path in ("/", "/analysis", "/settings", "/disks/sdb"):
            assert client.get(path).status_code == 200
        assert client.get("/api/health").status_code == 503
        collector = client.app.state.collector
        assert collector.sample()
        assert client.get("/api/health").status_code == 200
        collector.last_success_mono = time.monotonic()-100
        assert client.get("/api/health").status_code == 503
        assert client.get("/api/disks/nope").status_code == 404
        assert client.get("/api/disks/sdb/events?limit=201").status_code == 422
        settings = client.get("/api/settings").json()
        for key, value in [("sample_interval_seconds", 1), ("include_device_regex", "["), ("timezone", "nonsense"), ("timeouts_minutes", [0])]:
            assert client.put("/api/settings", json={"settings": {**settings, key:value}}).status_code == 422
        assert client.put("/api/settings", json={"settings":settings,"enabled_disks":{"oops":True}}).status_code == 422
        assert client.post("/api/data/clear", json={"confirmation":"yes"}).status_code == 422
        assert client.post("/api/data/clear", json={"confirmation":"CLEAR HISTORY"}, headers={"Origin":"https://other.test"}).status_code == 403


def test_twenty_four_disks_settings_exports_and_restart(rig):
    config, _, _ = rig
    names = [f"sd{chr(97+i)}" for i in range(24)]
    config.diskstats_path.write_text("".join(diskstats(name=n) for n in names))
    with TestClient(create_app(config, start_collector=False)) as client:
        collector = client.app.state.collector
        collector.sample(now=1000, monotonic=1000)
        config.diskstats_path.write_text("".join(diskstats(name=n, reads=12) for n in names))
        collector.sample(now=1030, monotonic=1030)
        assert len(client.get("/api/disks").json()) == 24
        settings = client.get("/api/settings").json()
        settings["timezone"] = "America/Toronto"
        result = client.put("/api/settings", json={"settings":settings, "enabled_disks":{"sda":False}})
        assert result.status_code == 200
        assert collector.wake_event.is_set()
        assert client.get("/api/analysis").json()["enabled_disks"] == 23
        for kind in ("idle-intervals.csv", "activity-events.csv", "timeout-analysis.csv"):
            response = client.get(f"/api/export/{kind}")
            assert response.status_code == 200
            rows = list(csv.DictReader(io.StringIO(response.text)))
            assert len(rows) == (96 if kind == "timeout-analysis.csv" else 24)
        exported = client.get("/api/export/complete.json").json()
        assert len(exported["activity_events"]) == 24
        assert len(exported["disks"]) == 24
        analysis_rows = list(csv.DictReader(io.StringIO(client.get("/api/export/timeout-analysis.csv").text)))
        detail = client.get("/api/disks/sda").json()
        assert float(analysis_rows[0]["spun_down_hours"]) == detail["timeouts"][0]["spun_down_hours"]
    with TestClient(create_app(config, start_collector=False)) as client:
        assert client.get("/api/settings").json()["timezone"] == "America/Toronto"
        assert client.get("/api/disks/sda").json()["enabled"] == 0
        assert len(client.get("/api/export/complete.json").json()["activity_events"]) == 24
        assert client.post("/api/data/clear",json={"confirmation":"CLEAR HISTORY"}).status_code == 200
        assert not client.get("/api/export/complete.json").json()["activity_events"]
        assert len(client.get("/api/disks").json()) == 24


def test_empty_exports_and_zero_states(rig):
    config, _, _ = rig
    with TestClient(create_app(config, start_collector=False)) as client:
        assert client.get("/api/disks").json() == []
        assert client.get("/api/status").json()["minimum_valid_observation_seconds"] == 0
        assert client.get("/api/export/timeout-analysis.csv").text.startswith("device_name,")
        assert client.get("/api/export/complete.json").json()["disks"] == []
