import json

import pytest
from fastapi.testclient import TestClient

from app.diskstats import Counters
from app.main import create_app
from app.request_capture import RequestCapture
from app.request_sources import ActivityMatcher, container_name, disk_mapping


def baseline(mapping=None):
    events = []
    matcher = ActivityMatcher(mapping or {'disk10': 'sdb'}, events.append)
    for second in range(61):
        matcher.sample({d: Counters(10, 100, 0, 0) for d in matcher.mapping.values()}, second, 1700000000+second)
    return matcher, events


def request(matcher, disk='disk10', cid='a'*64, stamp=60.5):
    matcher.request({'disk': disk, 'container_id': cid, 'process': 'Plex Transcoder', 'pid': 100, 'monotonic': stamp})


def resume(matcher, device='sdb', second=61):
    values = {d: Counters(10+(d == device), 100+8*(d == device), 0, 0) for d in matcher.mapping.values()}
    matcher.sample(values, second, 1700000000+second)
    matcher.sample(values, second+1, 1700000001+second)
    matcher.sample(values, second+2, 1700000002+second)


def test_plex_open_requires_resumed_disk_io_and_never_claims_spinup():
    matcher, events = baseline()
    request(matcher)
    resume(matcher)
    assert len(events) == 1
    assert events[0]['attribution'] == 'request_likely'
    assert events[0]['candidates'][0]['container_id'] == 'a'*64
    assert events[0]['array_disk'] == 'disk10'
    assert events[0]['bytes'] == 4096
    assert events[0]['quiet_seconds'] == 61
    assert events[0]['quiet_is_lower_bound'] is True
    assert events[0]['physical_spin_up_proven'] is False


def test_cached_open_without_counter_change_creates_no_source():
    matcher, events = baseline()
    request(matcher)
    for now in range(61, 80):
        matcher.sample({'sdb': Counters(10, 100, 0, 0)}, now, now)
    assert not events


def test_two_containers_on_same_disk_are_explicitly_ambiguous():
    matcher, events = baseline()
    request(matcher)
    request(matcher, cid='b'*64)
    resume(matcher)
    assert events[0]['attribution'] == 'request_multiple'
    assert events[0]['candidate_count'] == 2


def test_other_disk_and_old_requests_cannot_supply_a_cause():
    matcher, events = baseline({'disk10': 'sdb', 'disk4': 'sdc'})
    request(matcher, disk='disk4')
    request(matcher, stamp=10)
    resume(matcher)
    assert events[0]['attribution'] == 'request_unknown'
    assert events[0]['candidates'] == []


def test_concurrent_disk_requests_stay_separate():
    matcher, events = baseline({'disk10': 'sdb', 'disk4': 'sdc'})
    request(matcher)
    request(matcher, disk='disk4', cid='b'*64)
    for now in (61, 62, 63):
        matcher.sample({d: Counters(11, 108, 0, 0) for d in ('sdb','sdc')}, now, now)
    assert {e['array_disk']: e['candidates'][0]['container_id'] for e in events} == {'disk10':'a'*64, 'disk4':'b'*64}


def test_capture_stall_and_counter_reset_discard_quiet_history():
    matcher, events = baseline()
    request(matcher)
    resume(matcher, second=70)
    assert not events
    matcher, events = baseline()
    matcher.sample({'sdb': Counters(0, 0, 0, 0)}, 61, 61)
    resume(matcher, second=62)
    assert not events


def test_unraid_quoted_sections_map_only_assigned_array_disks():
    text = '["disk10"]\nname="disk10"\ndevice="sdb"\n["parity"]\ndevice="sda"\n["cache"]\ndevice="nvme0n1"\n'
    assert disk_mapping(text) == {'disk10': 'sdb'}
    with pytest.raises(ValueError, match='Ambiguous'):
        disk_mapping(text+'["disk11"]\ndevice="sdb"\n')


def test_docker_name_only_matches_verified_container_id(tmp_path):
    cid = 'a'*64
    (tmp_path/cid).mkdir()
    config = tmp_path/cid/'config.v2.json'
    config.write_text(json.dumps({'ID': cid, 'Name':'/plex', 'Config': {'Env':['SECRET=private']}}))
    assert container_name(tmp_path, cid) == 'plex'
    config.write_text(json.dumps({'ID': 'b'*64, 'Name':'/wrong'}))
    assert container_name(tmp_path, cid) is None


def test_new_sources_reach_main_table_and_history_without_legacy_rows(rig):
    config, _, _ = rig
    with TestClient(create_app(config, start_collector=False)) as client:
        assert client.app.state.collector.sample()
        matcher, events = baseline()
        request(matcher)
        resume(matcher)
        event = events[0]
        event['observed_at'] = __import__('time').time()
        event['candidates'][0]['container_name'] = 'plex'
        capture = RequestCapture(client.app.state.db, config)
        capture.persist(event)
        disk = client.get('/api/disks/sdb').json()
        assert disk['latest_source']['container_name'] == 'plex'
        assert disk['latest_source']['evidence']['physical_spin_up_proven'] is False
        assert len(client.get('/api/disks/sdb/sources').json()['events']) == 1
        assert client.post('/api/data/clear', json={'confirmation':'CLEAR HISTORY'}).status_code == 200
        assert client.get('/api/disks/sdb').json()['latest_source'] is None


def test_v3_source_history_migrates_without_losing_legacy_rows(rig):
    from app.database import Database
    config, db, collector = rig
    collector.sample()
    with db.connect() as con:
        con.execute("INSERT INTO attribution_events(disk_name,observed_at,process,pid,attribution,operation,bytes) VALUES ('sdb',1,'mdunraidd',1,'host_or_kernel','R',512)")
        con.execute('ALTER TABLE attribution_events DROP COLUMN evidence')
        con.execute('PRAGMA user_version=3')
    migrated = Database(db.path)
    row = migrated.rows('SELECT * FROM attribution_events')[0]
    assert row['process'] == 'mdunraidd' and row['evidence'] is None
    assert json.loads(''.join(migrated.export_json()))['schema_version'] == 4


def test_stopping_capture_terminates_worker_and_waits_for_cleanup(rig):
    config, db, _ = rig
    capture = RequestCapture(db, config)
    class Worker:
        terminated = False
        waited = False
        def poll(self): return None
        def terminate(self): self.terminated = True
        def wait(self, timeout): self.waited = timeout
    capture.process = worker = Worker()
    capture.stop()
    assert worker.terminated and worker.waited == 10
    assert capture.stopping.is_set()
