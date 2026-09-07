import json
from concurrent.futures import ThreadPoolExecutor


def test_stream_can_resume_on_different_workers_while_collector_writes(rig):
    _, db, collector = rig
    collector.sample(now=1000, monotonic=1000)
    stream = db.export_json()
    # Exhaust one next() at a time, just as Starlette's threadpool iterator does,
    # alternating two distinct worker threads while retaining the read snapshot.
    with ThreadPoolExecutor(max_workers=1) as first, ThreadPoolExecutor(max_workers=1) as second:
        chunks = [first.submit(next, stream).result()]
        collector.sample(now=1030, monotonic=1030)
        for index in range(100):
            value = (first if index % 2 else second).submit(next, stream, None).result()
            if value is None:
                break
            chunks.append(value)
    result = json.loads("".join(chunks))
    assert len(result["disks"]) == 1
    assert len(result["observation_sessions"]) == 1
    assert db.rows("SELECT last_observed_at FROM observation_sessions")[0]["last_observed_at"] == 1030
