import json
from pathlib import Path

from fastapi.testclient import TestClient
from app import attribution
from app.attribution import Attribution, parse_trace, process_source
from app.main import create_app
from conftest import poll


def trace(stamp=1000, device='8,16', process='Plex Media Serv', pid=123):
    return f' {process}-{pid} [008] ...1 {stamp:.6f}: block_bio_queue: {device} R 2048 + 8 [{process}]'


def test_parse_supplied_unraid_event_format():
    event = parse_trace(trace())
    assert event == dict(pid=123, monotonic=1000.0, device='8:16', operation='R', bytes=4096, process='Plex Media Serv')
    assert parse_trace('unrecognized trace') is None
    assert parse_trace(trace().replace('block_bio_queue:', 'block_rq_issue:')) is None
    assert parse_trace(trace(process='kworker/2:1H', pid=32))['process'] == 'kworker/2:1H'


def fake_process(root, cid, monkeypatch):
    monkeypatch.setattr(attribution.os, 'sysconf', lambda name: 100, raising=False)
    process = root/'123'
    process.mkdir(parents=True)
    fields = ['0']*20
    fields[19] = '1'
    (process/'stat').write_text('123 (Plex Media Serv) '+' '.join(fields))
    (process/'comm').write_text('Plex Media Serv\n')
    (process/'cgroup').write_text(f'0::/system.slice/docker-{cid}.scope\n')
    return process


def test_container_name_and_missing_metadata(tmp_path, monkeypatch):
    cid = 'a'*64
    proc, docker = tmp_path/'proc', tmp_path/'docker'
    fake_process(proc, cid, monkeypatch)
    source = process_source(proc, docker, parse_trace(trace()))
    assert source['container_id'] == cid
    assert source['container_name'] is None
    (docker/cid).mkdir(parents=True)
    (docker/cid/'config.v2.json').write_text(json.dumps({'ID': cid, 'Name': '/plex', 'Config': {'Env':['SECRET=never-export']}}))
    source = process_source(proc, docker, parse_trace(trace()))
    assert source == dict(container_id=cid, container_name='plex', attribution='container_cgroup')
    assert 'SECRET' not in json.dumps(source)


def test_exited_reused_and_host_process_are_not_guessed(tmp_path, monkeypatch):
    proc, docker = tmp_path/'proc', tmp_path/'docker'
    event = parse_trace(trace())
    assert process_source(proc, docker, event)['attribution'] == 'unresolved_process'
    process = fake_process(proc, 'b'*64, monkeypatch)
    (process/'comm').write_text('another process')
    assert process_source(proc, docker, event)['container_id'] is None
    (process/'comm').write_text(event['process'])
    (process/'cgroup').write_text('0::/system.slice/smbd.service')
    assert process_source(proc, docker, event)['attribution'] == 'host_or_kernel'
    (process/'cgroup').write_text('0::/docker/'+'b'*64)
    (process/'stat').write_text('123 (Plex Media Serv) '+' '.join(['0']*19+['999999999999']))
    assert process_source(proc, docker, event)['container_id'] is None


def test_capture_only_first_request_and_resumption_and_reset(rig, monkeypatch):
    config, db, collector = rig
    poll(collector, 0)
    config.host_proc_path = config.data_dir/'proc'
    config.docker_metadata_path = config.data_dir/'docker'
    capture = Attribution(db, config, collector)
    capture.devices = {'8:16':'sdb'}
    for stamp in (1000, 1010, 1020, 1080):
        capture.accept(parse_trace(trace(stamp)))
    rows = db.rows('SELECT * FROM attribution_events')
    assert [r['quiet_seconds'] for r in rows] == [None, 60]
    assert rows[1]['attribution'] == 'unresolved_process'
    capture.coverage_since = 1200
    capture.last.clear()
    capture.accept(parse_trace(trace(1190)))
    capture.accept(parse_trace(trace(1210)))
    assert [r['quiet_seconds'] for r in db.rows('SELECT * FROM attribution_events')] == [None, 60, None]
    with db.connect() as connection:
        connection.execute('UPDATE disks SET enabled=0')
    capture.accept(parse_trace(trace(2000)))
    assert len(db.rows('SELECT * FROM attribution_events')) == 3


def test_unavailable_trace_does_not_stop_profiler_and_export_clear(rig):
    config, _, _ = rig
    config.attribution_enabled = True
    config.tracefs_path = config.data_dir/'missing'
    with TestClient(create_app(config, start_collector=False)) as client:
        capture = client.app.state.attribution
        capture.run()
        assert client.get('/api/attribution/status').json()['state'] == 'unavailable'
        assert client.app.state.collector.sample()
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/disks/sdb/sources').json()['events'] == []
        assert client.get('/api/disks/sdb/sources?limit=101').status_code == 422
        assert client.get('/api/disks/missing/sources').status_code == 404
        with client.app.state.db.connect() as connection:
            connection.execute("INSERT INTO attribution_events(disk_name, observed_at, process, pid, attribution, operation, bytes) VALUES ('sdb',1,'test',1,'unresolved_process','R',512)")
        assert len(client.get('/api/export/complete.json').json()['attribution_events']) == 1
        assert 'test' in client.get('/api/export/activity-sources.csv').text
        assert client.post('/api/data/clear', json={'confirmation':'CLEAR HISTORY'}).status_code == 200
        assert client.get('/api/disks/sdb/sources').json()['events'] == []


def test_disabled_by_default(rig):
    config, _, _ = rig
    with TestClient(create_app(config, start_collector=False)) as client:
        assert client.get('/api/attribution/status').json()['state'] == 'disabled'


def test_dashboard_returns_latest_source_per_disk(rig):
    config, _, _ = rig
    with TestClient(create_app(config, start_collector=False)) as client:
        assert client.app.state.collector.sample()
        assert client.get('/api/disks').json()[0]['latest_source'] is None
        with client.app.state.db.connect() as connection:
            for name in ('old-container', 'latest-container'):
                connection.execute('''INSERT INTO attribution_events(disk_name,observed_at,process,pid,
                    container_name,attribution,operation,bytes) VALUES ('sdb',1,'process',1,?,'container_cgroup','R',512)''', (name,))
        source = client.get('/api/disks').json()[0]['latest_source']
        assert source['container_name'] == 'latest-container'
        assert source == client.get('/api/disks/sdb/sources').json()['events'][0]


def test_loss_breaks_continuity(rig, monkeypatch):
    config, db, collector = rig
    capture = Attribution(db, config, collector)
    capture.instance = config.data_dir/'instance'
    cpu = capture.instance/'per_cpu'/'cpu0'
    cpu.mkdir(parents=True)
    (cpu/'stats').write_text('overrun: 5\ndropped events: 2\ncommit overrun: 1\n')
    capture.last = {'sdb': 10}
    capture.check_loss()
    assert capture.dropped == 8
    assert capture.last == {}
    assert capture.coverage_since > 0


def test_isolated_setup_filters_selected_device_and_cleans_up(rig, monkeypatch):
    config, db, collector = rig
    poll(collector, 0)
    config.tracefs_path = config.data_dir/'tracing'
    (config.tracefs_path/'instances').mkdir(parents=True)
    (config.tracefs_path/'tracing_on').write_text('do-not-touch')
    disk = config.sys_block_path/'sdb'
    disk.mkdir(parents=True)
    (disk/'dev').write_text('8:16')
    original_mkdir = Path.mkdir

    def kernel_mkdir(path, *args, **kwargs):
        existed = path.exists()
        original_mkdir(path, *args, **kwargs)
        if not existed and path.parent == config.tracefs_path/'instances':
            # Simulate tracefs-populated control files; exercise actual open/write/close.
            for relative in ('tracing_on', 'current_tracer', 'trace_clock', 'buffer_size_kb',
                             'options/context-info', 'options/latency-format', 'options/print-tgid',
                             'options/disable_on_free', 'free_buffer', 'trace_pipe',
                             'events/block/block_bio_queue/enable', 'events/block/block_bio_queue/filter',
                             'events/block/block_bio_queue/format'):
                file = path/relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('')
            (path/'events/block/block_bio_queue/format').write_text('field:dev_t dev; field:char comm[16]; field:sector_t sector;')

    monkeypatch.setattr(Path, 'mkdir', kernel_mkdir)
    monkeypatch.setattr(attribution.os, 'O_NONBLOCK', getattr(attribution.os, 'O_NONBLOCK', 0), raising=False)
    capture = Attribution(db, config, collector)
    capture.setup()
    assert capture.state == 'capturing'
    assert (capture.instance/'events/block/block_bio_queue/filter').read_text() == f'dev == {(8<<20)|16}'
    assert (capture.instance/'tracing_on').read_text() == '1'
    capture.cleanup()
    assert (capture.instance/'tracing_on').read_text() == '0'
    assert (capture.instance/'events/block/block_bio_queue/enable').read_text() == '0'
    assert (config.tracefs_path/'tracing_on').read_text() == 'do-not-touch'
    assert capture.fd is capture.free_fd is None
