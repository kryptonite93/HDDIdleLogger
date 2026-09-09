from app.analysis import analyze
from conftest import poll


def summarize(db, settings):
    return analyze(db.rows('SELECT * FROM idle_intervals'), db.rows('SELECT * FROM observation_sessions'), settings)


def test_active_polls_do_not_dominate_quiet_median(rig):
    _, db, collector = rig
    poll(collector, 0)
    for second in (30, 60, 90):
        poll(collector, second, reads=10+second//30)
    for second in range(120, 28921, 30):
        poll(collector, second, reads=13 if second < 28920 else 14)
    result = summarize(db, collector.settings)
    assert result['median_idle_seconds'] == 28830
    assert result['completed_interval_count'] == 1
    assert result['excluded_active_gaps'] == 2
    assert result['timeouts'][1]['completed_spin_ups'] == 1


def test_delayed_active_sample_is_not_a_quiet_period(rig):
    _, db, collector = rig
    poll(collector, 0)
    poll(collector, 30, reads=11)
    poll(collector, 90, reads=12)
    assert summarize(db, collector.settings)['median_idle_seconds'] is None
    poll(collector, 120, reads=12)
    poll(collector, 150, reads=13)
    assert summarize(db, collector.settings)['median_idle_seconds'] == 60


def test_longest_includes_ongoing_without_contaminating_median(rig):
    _, db, collector = rig
    poll(collector, 0)
    poll(collector, 30, reads=11)
    poll(collector, 60, reads=11)
    poll(collector, 90, reads=12)
    for second in range(120, 721, 30):
        poll(collector, second, reads=12)
    result = summarize(db, collector.settings)
    assert result['longest_idle_seconds'] == 630
    assert result['longest_idle_is_ongoing'] is True
    assert result['longest_completed_idle_seconds'] == 60
    assert result['median_idle_seconds'] == 60
    collector.stop()
    result = summarize(db, collector.settings)
    assert result['longest_idle_seconds'] == 630
    assert result['longest_idle_is_ongoing'] is False
    assert result['longest_idle_is_lower_bound'] is True


def test_unknown_initial_idle_is_visible_but_not_a_completed_statistic(rig):
    _, db, collector = rig
    for second in (0, 30, 60):
        poll(collector, second)
    result = summarize(db, collector.settings)
    assert result['longest_idle_seconds'] == 60
    assert result['longest_idle_is_lower_bound'] is True
    assert result['median_idle_seconds'] is None
    assert result['timeouts'][0]['completed_spin_ups'] == 0


def test_legacy_statistics_use_session_cadence_and_report_estimates():
    from app.config import Settings
    sessions = [{'id':1,'started_at':0,'last_observed_at':3000,'ended_at':3000,'sample_interval_seconds':60}]
    intervals = [{'session_id':1,'duration_seconds':v,'start_is_censored':0,'end_is_censored':0}
                 for v in (60, 61, 1800)]
    result = analyze(intervals, sessions, Settings(sample_interval_seconds=10))
    assert result['median_idle_seconds'] == 1800
    assert result['estimated_quiet_interval_count'] == 1
    assert result['excluded_active_gaps'] == 2


def test_upgrade_preserves_legacy_history_and_settings(rig):
    import json
    from app.database import Database

    _, db, collector = rig
    db.save_settings(collector.settings)
    for second, reads in ((0, 10), (30, 11), (60, 11), (90, 12)):
        poll(collector, second, reads=reads)
    collector.stop()
    # Recreate the v1 layout, which had no quiet-poll evidence column.
    with db.connect() as connection:
        connection.execute('DROP TABLE attribution_events')
        connection.execute('ALTER TABLE idle_intervals DROP COLUMN quiet_sample_observed')
        connection.execute('PRAGMA user_version=1')
    tables = ('settings', 'disks', 'observation_sessions', 'activity_events', 'idle_intervals', 'collector_events')
    before = {table: db.rows(f'SELECT * FROM {table}') for table in tables}
    for _ in range(2):
        upgraded = Database(db.path)
        exported = json.loads(''.join(upgraded.export_json()))
        assert exported['schema_version'] == 3
        for table in tables:
            rows = exported[table]
            if table == 'idle_intervals':
                assert all(r.pop('quiet_sample_observed') is None for r in rows)
            assert rows == before[table]
        assert summarize(upgraded, collector.settings)['median_idle_seconds'] == 60


def test_api_exposes_ongoing_longest_and_quiet_stats(rig):
    from fastapi.testclient import TestClient
    from app.main import create_app

    config, _, _ = rig
    with TestClient(create_app(config, start_collector=False)) as client:
        collector = client.app.state.collector
        for second, reads in ((0, 10), (30, 11), (60, 12), (90, 12), (120, 13), (150, 13), (180, 13), (210, 13)):
            poll(collector, second, reads=reads)
        result = client.get('/api/disks/sdb').json()
        assert result['median_idle_seconds'] == 60
        assert result['longest_idle_seconds'] == result['current_idle_seconds'] == 90
        assert result['longest_idle_is_ongoing'] is True
        assert result['excluded_active_gaps'] == 1
        assert sum(b['count'] for b in result['histogram']) == 1
        exported = client.get('/api/export/complete.json').json()
        assert exported['schema_version'] == 3
        assert [i['quiet_sample_observed'] for i in exported['idle_intervals']] == [0, 0, 1]
