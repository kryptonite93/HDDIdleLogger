from fastapi.testclient import TestClient
from app.main import create_app


def test_old_enabled_flag_cannot_restart_tracing(rig, monkeypatch):
    config, _, _ = rig
    monkeypatch.setenv('ATTRIBUTION_ENABLED', 'true')
    with TestClient(create_app(config, start_collector=False)) as client:
        assert client.app.state.collector.sample()
        assert client.get('/api/health').status_code == 200
        status = client.get('/api/attribution/status').json()
        assert status['enabled'] is False
        assert status['state'] == 'retired'
        assert not hasattr(client.app.state.attribution, 'start')


def test_worker_history_is_exportable_but_not_shown_as_a_source(rig):
    config, _, _ = rig
    with TestClient(create_app(config, start_collector=False)) as client:
        assert client.app.state.collector.sample()
        with client.app.state.db.connect() as connection:
            connection.execute("INSERT INTO attribution_events(disk_name,observed_at,process,pid,attribution,operation,bytes) VALUES ('sdb',1,'mdunraidd4',1,'host_or_kernel','R',512)")
        assert client.get('/api/disks').json()[0]['latest_source'] is None
        assert client.get('/api/disks/sdb/sources').json()['events'] == []
        assert client.get('/api/disks/missing/sources').status_code == 404
        assert len(client.get('/api/export/complete.json').json()['attribution_events']) == 1
        assert 'mdunraidd4' in client.get('/api/export/activity-sources.csv').text
        assert client.post('/api/data/clear', json={'confirmation': 'CLEAR HISTORY'}).status_code == 200
        assert client.get('/api/export/complete.json').json()['attribution_events'] == []
