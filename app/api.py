import csv
import io
import time
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from .analysis import analyze, array_analysis
from .config import Settings

router = APIRouter(prefix="/api")


def snapshot(request):
    collector = request.app.state.collector
    db = request.app.state.db
    # Read all related tables at one WAL snapshot; do analysis after releasing it.
    with db.connect() as connection:
        connection.execute("BEGIN")
        disks = [dict(r) for r in connection.execute("SELECT * FROM disks ORDER BY device_name")]
        sessions, intervals = defaultdict(list), defaultdict(list)
        for row in connection.execute("SELECT * FROM observation_sessions"):
            sessions[row["disk_name"]].append(dict(row))
        for row in connection.execute("SELECT * FROM idle_intervals"):
            intervals[row["disk_name"]].append(dict(row))
        for disk in disks:
            event = connection.execute("SELECT * FROM activity_events WHERE disk_name=? ORDER BY id DESC LIMIT 1",
                                       (disk["device_name"],)).fetchone()
            disk["last_io"] = dict(event) if event else None
    for disk in disks:
        name = disk["device_name"]
        disk.update(analyze(intervals[name], sessions[name], collector.settings))
        current = next((s for s in reversed(sessions[name]) if s["ended_at"] is None), None)
        disk["current_idle_seconds"] = max(0, current["last_observed_at"]-current["idle_started_at"]) if current else None
        disk["current_idle_is_censored"] = current is not None and current["last_activity_at"] is None
        disk["last_activity_at"] = disk["last_io"]["observed_at"] if disk["last_io"] else None
        disk["state"] = ("Missing" if not disk["present"] else "Excluded" if not disk["eligible"] else
                         "Disabled" if not disk["enabled"] else "Paused" if not current or not collector.healthy() else
                         "Active recently" if disk["current_idle_seconds"] <= collector.settings.sample_interval_seconds else "Idle")
    return disks


@router.get("/health")
def health(request: Request):
    collector = request.app.state.collector
    ready = collector.healthy()
    return JSONResponse({"healthy": ready, "last_success_at": collector.last_success_at,
                         "error": collector.error}, status_code=200 if ready else 503)


@router.get("/status")
def status(request: Request):
    collector = request.app.state.collector
    disks = snapshot(request)
    return {"healthy": collector.healthy(), "error": collector.error,
            "uptime_seconds": time.monotonic()-collector.started_mono,
            "last_success_at": collector.last_success_at, "discovered_disks": len(disks),
            "idle_over_30_minutes": sum(d["state"] == "Idle" and (d["current_idle_seconds"] or 0) > 1800 for d in disks),
            "sample_interval_seconds": collector.settings.sample_interval_seconds,
            "timezone": collector.settings.timezone, **array_analysis(disks, collector.settings)}


@router.get("/disks")
def disks(request: Request):
    return snapshot(request)


@router.get("/disks/{name}")
def disk(name: str, request: Request):
    found = next((d for d in snapshot(request) if d["device_name"] == name), None)
    if found is None:
        raise HTTPException(404, "Disk not found")
    return found


@router.get("/disks/{name}/events")
def events(name: str, request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    db = request.app.state.db
    if not db.rows("SELECT 1 FROM disks WHERE device_name=?", (name,)):
        raise HTTPException(404, "Disk not found")
    return {"offset": offset, "limit": limit,
            "activity": db.rows("SELECT * FROM activity_events WHERE disk_name=? ORDER BY id DESC LIMIT ? OFFSET ?", (name, limit, offset)),
            "idle": db.rows("SELECT * FROM idle_intervals WHERE disk_name=? ORDER BY id DESC LIMIT ? OFFSET ?", (name, limit, offset)),
            "collector": db.rows("SELECT * FROM collector_events WHERE disk_name=? ORDER BY id DESC LIMIT ? OFFSET ?", (name, limit, offset))}


@router.get("/analysis")
def analysis(request: Request):
    return array_analysis(snapshot(request), request.app.state.collector.settings)


@router.get("/settings")
def settings(request: Request):
    return request.app.state.collector.settings


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: Settings
    enabled_disks: dict[str, bool] | None = None


@router.put("/settings")
def update_settings(update: SettingsUpdate, request: Request):
    collector, db = request.app.state.collector, request.app.state.db
    with collector.lock:
        known = {row["device_name"] for row in db.rows("SELECT device_name FROM disks")}
        if update.enabled_disks and set(update.enabled_disks)-known:
            raise HTTPException(422, "Unknown disk in enabled_disks")
        with db.connect() as connection:
            # Changing selection or cadence starts a new proven observation segment.
            changed_collection = any(getattr(update.settings, key) != getattr(collector.settings, key)
                                     for key in ("sample_interval_seconds", "include_device_regex", "exclude_device_regex"))
            if changed_collection:
                collector.close_all(connection, "settings_change", time.time())
            for name, enabled in (update.enabled_disks or {}).items():
                previous = connection.execute("SELECT enabled FROM disks WHERE device_name=?", (name,)).fetchone()[0]
                if previous != enabled:
                    for row in connection.execute("SELECT * FROM observation_sessions WHERE disk_name=? AND ended_at IS NULL", (name,)).fetchall():
                        collector.close(connection, row, "selection_change", time.time())
                connection.execute("UPDATE disks SET enabled=? WHERE device_name=?", (int(enabled), name))
            connection.execute("INSERT OR REPLACE INTO settings VALUES ('config', ?)", (update.settings.model_dump_json(),))
        collector.settings = update.settings
        collector.wake_event.set()
    return {"saved": True, "settings": update.settings}


class ClearConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["CLEAR HISTORY"]


@router.post("/data/clear")
def clear_data(body: ClearConfirmation, request: Request):
    collector, db = request.app.state.collector, request.app.state.db
    with collector.lock, db.connect() as connection:
        for table in ("activity_events", "idle_intervals", "observation_sessions", "collector_events"):
            connection.execute(f"DELETE FROM {table}")
        collector.last_success_at = collector.last_success_mono = None
        collector.previous_mono = collector.previous_wall = None
        collector.wake_event.set()
    return {"cleared": True, "message": "History cleared. Disk selection and settings retained."}


def csv_rows(rows, columns):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    yield output.getvalue()
    for row in rows:
        output.seek(0)
        output.truncate(0)
        writer.writerow(row)
        yield output.getvalue()


@router.get("/export/{kind}")
def export(kind: str, request: Request):
    db = request.app.state.db
    if kind == "complete.json":
        return StreamingResponse(db.export_json(), media_type="application/json",
                                 headers={"Content-Disposition": 'attachment; filename="hdd-idle-profiler.json"'})
    if kind == "timeout-analysis.csv":
        data = [{"device_name": d["device_name"], "enabled": d["enabled"], "eligible": d["eligible"],
                 "present": d["present"], "valid_observation_seconds": d["valid_observation_seconds"], **r}
                for d in snapshot(request) for r in d["timeouts"]]
        columns = list(data[0]) if data else ["device_name", "timeout_minutes", "completed_spin_ups", "spun_down_hours"]
        stream = csv_rows(data, columns)
    elif kind in ("idle-intervals.csv", "activity-events.csv"):
        table = {"idle-intervals.csv": "idle_intervals", "activity-events.csv": "activity_events"}[kind]

        def generate():
            with db.connect() as connection:
                cursor = connection.execute(f"SELECT * FROM {table} ORDER BY id")
                yield from csv_rows((dict(r) for r in cursor), [d[0] for d in cursor.description])
        stream = generate()
    else:
        raise HTTPException(404, "Unknown export")
    return StreamingResponse(stream, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{kind}"'})
