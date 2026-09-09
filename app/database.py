import json
import sqlite3
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS disks (
 device_name TEXT PRIMARY KEY, model TEXT, serial TEXT, vendor TEXT, rotational INTEGER,
 enabled INTEGER NOT NULL, eligible INTEGER NOT NULL DEFAULT 1, present INTEGER NOT NULL DEFAULT 1,
 first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS observation_sessions (
 id INTEGER PRIMARY KEY, disk_name TEXT NOT NULL REFERENCES disks(device_name),
 started_at REAL NOT NULL, last_observed_at REAL NOT NULL, ended_at REAL,
 boot_id TEXT, sample_interval_seconds INTEGER NOT NULL,
 counters TEXT NOT NULL, last_activity_at REAL, idle_started_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS activity_events (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES observation_sessions(id),
 disk_name TEXT NOT NULL REFERENCES disks(device_name), observed_at REAL NOT NULL,
 reads_delta INTEGER NOT NULL, writes_delta INTEGER NOT NULL,
 bytes_read_delta INTEGER NOT NULL, bytes_written_delta INTEGER NOT NULL,
 discards_delta INTEGER NOT NULL, bytes_discarded_delta INTEGER NOT NULL,
 flushes_delta INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS idle_intervals (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES observation_sessions(id),
 disk_name TEXT NOT NULL REFERENCES disks(device_name), started_at REAL NOT NULL,
 ended_at REAL NOT NULL, duration_seconds REAL NOT NULL,
 start_is_censored INTEGER NOT NULL, end_is_censored INTEGER NOT NULL DEFAULT 0,
 quiet_sample_observed INTEGER);
CREATE TABLE IF NOT EXISTS collector_events (
 id INTEGER PRIMARY KEY, disk_name TEXT, occurred_at REAL NOT NULL,
 event_type TEXT NOT NULL, details TEXT);
CREATE TABLE IF NOT EXISTS attribution_events (
 id INTEGER PRIMARY KEY, disk_name TEXT NOT NULL REFERENCES disks(device_name),
 observed_at REAL NOT NULL, quiet_seconds REAL, process TEXT NOT NULL, pid INTEGER NOT NULL,
 container_id TEXT, container_name TEXT, attribution TEXT NOT NULL,
 operation TEXT NOT NULL, bytes INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS attribution_disk_time ON attribution_events(disk_name, id);
CREATE INDEX IF NOT EXISTS activity_disk_time ON activity_events(disk_name, observed_at);
CREATE INDEX IF NOT EXISTS idle_disk_time ON idle_intervals(disk_name, ended_at);
CREATE INDEX IF NOT EXISTS session_disk ON observation_sessions(disk_name, ended_at);
"""


class Database:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 3:
                raise ValueError("Database was created by a newer version")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(idle_intervals)")}
            if "quiet_sample_observed" not in columns:
                connection.execute("ALTER TABLE idle_intervals ADD COLUMN quiet_sample_observed INTEGER")
            connection.execute("PRAGMA user_version=3")

    @contextmanager
    def connect(self):
        # StreamingResponse may resume a synchronous iterator on another worker.
        # Each connection still has one sequential owner; connections are not pooled.
        connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def rows(self, query, parameters=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def load_settings(self, defaults):
        rows = self.rows("SELECT value FROM settings WHERE key='config'")
        return type(defaults).model_validate_json(rows[0]["value"]) if rows else defaults

    def save_settings(self, settings):
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO settings VALUES ('config', ?)",
                               (settings.model_dump_json(),))

    def checkpoint(self):
        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def export_json(self):
        # A dedicated WAL read snapshot keeps export consistent without blocking the writer.
        with self.connect() as connection:
            connection.execute("BEGIN")
            yield '{"schema_version":3'
            for table in ("settings", "disks", "observation_sessions", "activity_events",
                          "idle_intervals", "collector_events", "attribution_events"):
                yield ',' + json.dumps(table) + ':['
                first = True
                for row in connection.execute(f"SELECT * FROM {table}"):
                    yield ("" if first else ",") + json.dumps(dict(row))
                    first = False
                yield ']'
            yield '}'
