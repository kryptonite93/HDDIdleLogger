import json
import logging
import threading
import time
from dataclasses import asdict

from .diskstats import Counters, eligible, metadata, parse_diskstats

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, db, config, settings):
        self.db, self.config, self.settings = db, config, settings
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.last_success_mono = None
        self.last_success_at = None
        self.error = None
        self.started_mono = time.monotonic()
        self.previous_mono = None
        self.previous_wall = None
        self.boot_id = self.read_boot()
        self.samples = 0
        # A restart cannot prove continuous observation, even with unchanged counters.
        # Keep the checkpointed prior segment, then start a fresh baseline.
        with db.connect() as connection:
            self.close_all(connection, "restart", time.time())

    def read_boot(self):
        try:
            return self.config.boot_id_path.read_text().strip()
        except OSError:
            return None

    def event(self, connection, name, kind, now, details=None):
        connection.execute("INSERT INTO collector_events(disk_name,occurred_at,event_type,details) VALUES (?,?,?,?)",
                           (name, now, kind, details))

    def close(self, connection, row, reason, now):
        end = row["last_observed_at"]
        connection.execute("UPDATE observation_sessions SET ended_at=? WHERE id=?", (end, row["id"]))
        if end > row["idle_started_at"]:
            connection.execute("""INSERT INTO idle_intervals(session_id,disk_name,started_at,ended_at,
             duration_seconds,start_is_censored,end_is_censored,quiet_sample_observed) VALUES (?,?,?,?,?,?,1,1)""",
             (row["id"], row["disk_name"], row["idle_started_at"], end,
              end-row["idle_started_at"], int(row["last_activity_at"] is None)))
        self.event(connection, row["disk_name"], reason, now)

    def close_all(self, connection, reason, now):
        for row in connection.execute("SELECT * FROM observation_sessions WHERE ended_at IS NULL").fetchall():
            self.close(connection, row, reason, now)

    def sample(self, now=None, monotonic=None):
        now = time.time() if now is None else now
        mono = time.monotonic() if monotonic is None else monotonic
        with self.lock:
            try:
                parsed = parse_diskstats(self.config.diskstats_path.read_text())
                if not parsed:
                    raise ValueError("No valid diskstats rows; check the read-only host mount")
                boot = self.read_boot()
                gap = self.previous_mono is not None and mono - self.previous_mono > 3*self.settings.sample_interval_seconds
                clock_changed = self.previous_mono is not None and abs(
                    (now-self.previous_wall)-(mono-self.previous_mono)) > 2
                with self.db.connect() as connection:
                    if gap or clock_changed or boot != self.boot_id:
                        self.close_all(connection, "sampling_gap" if gap else "clock_change" if clock_changed else "boot_change", now)
                    connection.execute("UPDATE disks SET present=0")
                    active = {r["disk_name"]: r for r in connection.execute(
                        "SELECT * FROM observation_sessions WHERE ended_at IS NULL")}
                    for name, counters in parsed.items():
                        # Record physical disk candidates even when excluded, so settings explain why.
                        import re
                        if not re.fullmatch(r"sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+", name):
                            continue
                        info = metadata(self.config.sys_block_path, name)
                        known = connection.execute("SELECT rotational FROM disks WHERE device_name=?", (name,)).fetchone()
                        if info["rotational"] is None and known is not None:
                            info["rotational"] = known["rotational"]
                        allowed = eligible(name, info, self.settings, self.config.sys_block_path)
                        connection.execute("""INSERT INTO disks VALUES (?,?,?,?,?,?,?,1,?,?)
                         ON CONFLICT(device_name) DO UPDATE SET model=COALESCE(excluded.model,disks.model),
                         serial=COALESCE(excluded.serial,disks.serial),vendor=COALESCE(excluded.vendor,disks.vendor),
                         rotational=COALESCE(excluded.rotational,disks.rotational),eligible=excluded.eligible,
                         present=1,last_seen_at=excluded.last_seen_at""",
                         (name, info["model"], info["serial"], info["vendor"], info["rotational"],
                          int(allowed), int(allowed), now, now))
                        disk = connection.execute("SELECT * FROM disks WHERE device_name=?", (name,)).fetchone()
                        previous = active.pop(name, None)
                        if not allowed or not disk["enabled"]:
                            if previous:
                                self.close(connection, previous, "disabled_or_filtered", now)
                            continue
                        delta = counters.delta(Counters(**json.loads(previous["counters"]))) if previous else None
                        if previous and delta is None:
                            self.close(connection, previous, "counter_reset", now)
                            previous = None
                        if previous is None:
                            connection.execute("""INSERT INTO observation_sessions(disk_name,started_at,last_observed_at,
                             boot_id,sample_interval_seconds,counters,idle_started_at) VALUES (?,?,?,?,?,?,?)""",
                             (name, now, now, boot, self.settings.sample_interval_seconds, json.dumps(asdict(counters)), now))
                            self.event(connection, name, "baseline", now)
                            continue
                        if any(delta.values()):
                            duration = now - previous["idle_started_at"]
                            connection.execute("""INSERT INTO idle_intervals(session_id,disk_name,started_at,ended_at,
                             duration_seconds,start_is_censored,quiet_sample_observed) VALUES (?,?,?,?,?,?,?)""",
                             (previous["id"], name, previous["idle_started_at"], now, duration,
                              int(previous["last_activity_at"] is None),
                              int(previous["last_observed_at"] > previous["idle_started_at"])))
                            connection.execute("""INSERT INTO activity_events(session_id,disk_name,observed_at,
                             reads_delta,writes_delta,bytes_read_delta,bytes_written_delta,discards_delta,
                             bytes_discarded_delta,flushes_delta) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                             (previous["id"], name, now, delta["reads"], delta["writes"], delta["sectors_read"]*512,
                              delta["sectors_written"]*512, delta["discards"], delta["sectors_discarded"]*512, delta["flushes"]))
                            connection.execute("UPDATE observation_sessions SET last_activity_at=?,idle_started_at=? WHERE id=?",
                                               (now, now, previous["id"]))
                        connection.execute("UPDATE observation_sessions SET last_observed_at=?,counters=? WHERE id=?",
                                           (now, json.dumps(asdict(counters)), previous["id"]))
                    for row in active.values():
                        self.close(connection, row, "device_missing", now)
                self.last_success_at, self.last_success_mono = now, mono
                self.previous_wall, self.previous_mono, self.boot_id = now, mono, boot
                self.error = None
                self.samples += 1
                if self.samples % 120 == 0:
                    self.db.checkpoint()
                return True
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                log.warning("Collection failed: %s", exc)
                # First failed poll breaks continuity, even if recovery is quick.
                with self.db.connect() as connection:
                    self.close_all(connection, "source_error", now)
                return False

    def healthy(self):
        return self.last_success_mono is not None and time.monotonic()-self.last_success_mono <= 3*self.settings.sample_interval_seconds

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.sample()
            except Exception:
                log.exception("Collector stopped after unexpected failure")
                self.error = "Collector failed; check container logs"
                return
            self.wake_event.wait(self.settings.sample_interval_seconds)
            self.wake_event.clear()

    def start(self):
        self.thread = threading.Thread(target=self.run, name="diskstats-collector", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join()
        with self.lock, self.db.connect() as connection:
            self.close_all(connection, "shutdown", time.time())
        self.db.checkpoint()
