# Unraid HDD Idle Profiler

## Project handoff for Codex

**Working name:** Unraid HDD Idle Profiler  
**Project type:** Self-hosted monitoring web app  
**Deployment target:** Docker container on Unraid  
**Distribution:** Docker image plus an Unraid Community Applications-compatible XML template hosted in the owner's GitHub repository  
**Primary goal:** Measure real per-drive I/O patterns for several days or weeks and show which Unraid spin-down delay is likely to save the most energy without causing excessive spin cycles.

---

## 1. Context and user goal

The owner has an Unraid media server with more than 20 spinning hard drives and roughly 200 TB of storage. Torrents are now removed from the active client sooner, so many array disks may remain unused for long periods. Before enabling or changing Unraid's disk spin-down timer, the owner wants evidence showing how long each drive actually sits idle.

This app should run continuously for a short profiling period, usually a few days to a week, and answer:

- How long has each disk currently been idle?
- What are the longest, median, and typical idle periods for each disk?
- How often does each disk resume activity after being idle for 15, 30, 60, or 120 minutes?
- For each hypothetical timeout, how many spin-down/spin-up cycles would likely occur?
- For each timeout, how many hours per day would a drive likely be spun down?
- Which timeout appears to be the best compromise for each disk and for the array overall?

Stable disk identity across reboots is not an MVP requirement. Tracking current Linux device names such as `sdb`, `sdc`, and `sdd` is acceptable because the intended tests last days or a week and the server may run for months without rebooting. The app should nevertheless display model/serial information when it can read it safely, so a later version can add stable identity without redesigning the database.

---

## 2. Product boundaries

### MVP must do

1. Sample Linux block-device counters without reading data from the disks.
2. Track only selected physical HDDs and exclude loop, RAM, optical, NVMe, SSD, Unraid parity pseudo-devices, and device-mapper devices by default.
3. Persist observations and derived idle intervals in SQLite.
4. Serve a responsive web UI from the same container.
5. Show live per-disk state and historical idle analysis.
6. Simulate spin-down timeouts of 15, 30, 60, and 120 minutes, plus optional user-defined values.
7. Export the useful results as CSV and JSON.
8. Survive an app/container restart without losing collected history.
9. Ship with Docker build files and a valid Unraid XML template suitable for hosting on GitHub.

### MVP must not do

- Do not issue spin-down, spin-up, `hdparm`, or SMART commands.
- Do not change Unraid settings.
- Do not inspect files or shares to infer access.
- Do not continuously store one database row per disk per sample.
- Do not require privileged container mode.
- Do not require Internet access, cloud accounts, telemetry, or external services.
- Do not promise that a kernel I/O event equals a platter wake-up. The MVP models likely behavior from block I/O activity; it does not measure actual drive power state.

### Possible later features

- Optional non-waking power-state checks using a carefully validated method such as `smartctl -n standby`.
- Stable disk mapping by serial number across host reboots.
- Energy and cost estimates based on per-drive active/standby watts and local electricity rates.
- Unraid API integration to read or apply per-disk spin-down settings.
- Notifications when a container or process repeatedly wakes disks.
- Process attribution using eBPF or audit tooling; this is explicitly out of MVP scope.

---

## 3. Recommended implementation

Use a deliberately small Python application:

- **Python 3.12**
- **FastAPI** for HTTP routes and JSON endpoints
- **Uvicorn** as the application server
- **Jinja2** templates for the main HTML shell
- **Vanilla JavaScript and CSS** for live updates and interaction
- **Chart.js vendored into the image** for charts; do not depend on a public CDN
- **SQLite** for persistence
- **pytest** for unit and integration tests

Avoid a separate Node build and avoid a frontend framework for the first version. The dashboard is small enough that server-rendered HTML plus a few JSON endpoints is easier to maintain and produces a smaller, more dependable container.

Suggested repository layout:

```text
unraid-hdd-idle-profiler/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── collector.py
│   ├── diskstats.py
│   ├── analysis.py
│   ├── database.py
│   ├── models.py
│   ├── api.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   └── disk.html
│   └── static/
│       ├── app.css
│       ├── app.js
│       └── vendor/chart.umd.min.js
├── tests/
│   ├── fixtures/
│   ├── test_diskstats.py
│   ├── test_collector.py
│   ├── test_analysis.py
│   └── test_api.py
├── unraid/
│   └── unraid-hdd-idle-profiler.xml
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .dockerignore
├── .gitignore
├── LICENSE
└── README.md
```

---

## 4. Data source and safe collection

### Primary source

Read the host's `/proc/diskstats` every 30 seconds by default. The container should receive it as a read-only bind mount at:

```text
/host/proc/diskstats
```

Optionally mount host `/sys/block` read-only at:

```text
/host/sys/block
```

`/sys/block/<device>/device/model`, `vendor`, and `serial` may be used only for display and device filtering. Reading these sysfs attributes should not cause disk I/O. Missing fields must not stop collection.

The path to diskstats must be configurable so development and tests can use fixture files.

### Relevant counters

Parse the standard fields needed to detect activity:

- reads completed
- sectors read
- writes completed
- sectors written
- optionally discards completed and sectors discarded
- optionally flush requests completed

Treat a disk as active in a sample interval when any monitored cumulative counter increases. Calculate bytes using the Linux diskstats sector convention of 512 bytes per sector; do not assume the drive's physical sector size.

Handle these conditions safely:

- Counter reset or decrease: record a discontinuity and reset that disk's baseline; do not interpret it as negative I/O.
- Device appears: begin observing it with an unknown prior idle duration.
- Device disappears: close or suspend its current observation without inventing an activity event.
- Application restart: restore last known counters and timestamps where possible. If the source has reset or the system boot changed, begin a new observation segment.
- Sampling delay: calculations must use timestamps, not an assumed exact 30-second cadence.

### Default device filtering

The app should auto-discover devices present in diskstats, then include conventional whole-disk SATA/SAS devices such as `sd[a-z]+` by default. Exclude partitions and these prefixes by default:

```text
loop, ram, zram, fd, sr, md, dm-, nvme
```

Provide include and exclude regex settings. The UI should allow the user to enable or disable discovered disks without restarting the container. Never hardcode exactly 20 disks.

### Why collection is non-waking

The app reads kernel-maintained counters and read-only device metadata. It must not open filesystem paths on the array disks, run SMART tests, or issue ATA commands. Add a clear note in the UI and README that the collector itself is designed not to wake an idle disk.

---

## 5. Event and interval model

Avoid writing one row for every 30-second sample. For 20 or more disks, samples are useful in memory but unnecessarily noisy in storage.

Persist:

1. Observation sessions/segments.
2. Discovered disk metadata and enabled state.
3. Activity events containing the timestamp and counter deltas.
4. Completed idle intervals between activity events.
5. Collector discontinuities, restarts, or counter resets.
6. Settings.

Suggested SQLite schema:

```sql
CREATE TABLE observation_sessions (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    boot_id TEXT,
    sample_interval_seconds INTEGER NOT NULL
);

CREATE TABLE disks (
    id INTEGER PRIMARY KEY,
    device_name TEXT NOT NULL,
    model TEXT,
    serial TEXT,
    vendor TEXT,
    rotational INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE activity_events (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    disk_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    reads_delta INTEGER NOT NULL DEFAULT 0,
    writes_delta INTEGER NOT NULL DEFAULT 0,
    bytes_read_delta INTEGER NOT NULL DEFAULT 0,
    bytes_written_delta INTEGER NOT NULL DEFAULT 0,
    discards_delta INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(session_id) REFERENCES observation_sessions(id),
    FOREIGN KEY(disk_id) REFERENCES disks(id)
);

CREATE TABLE idle_intervals (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    disk_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    start_is_censored INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(session_id) REFERENCES observation_sessions(id),
    FOREIGN KEY(disk_id) REFERENCES disks(id)
);

CREATE TABLE collector_events (
    id INTEGER PRIMARY KEY,
    session_id INTEGER,
    disk_id INTEGER,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

An idle interval is the gap between observed activity events, not a claim that the drive was physically asleep. The interval from app startup until the first observed activity is left-censored: the app does not know when the preceding activity occurred. Retain it for current-idle display but exclude it from median/recommendation calculations unless clearly labelled.

The currently open idle interval can be calculated from the last activity timestamp to `now`; it does not need a continuously updated database row.

---

## 6. Timeout simulation

For a completed idle interval of length `G` seconds and a hypothetical spin-down timeout `T`:

- A modeled spin-down occurs if `G > T`.
- Modeled spun-down duration is `max(0, G - T)`.
- One modeled spin-up occurs when activity resumes after that modeled spin-down.

For the currently open interval:

- Include `max(0, current_idle - T)` in a clearly labelled current/potential spun-down duration.
- Do not count a spin-up until later activity actually occurs.

For each disk and timeout report:

- modeled spin-down opportunities
- modeled completed spin-ups
- modeled spin-ups per observed day
- total modeled spun-down hours
- modeled spun-down hours per observed day
- percentage of valid observed time beyond the timeout

Show both raw totals and daily-normalized figures. Exclude gaps that cross collector downtime, counter resets, host boots, or other discontinuities.

### Recommendation logic

Recommendations must be transparent and configurable rather than presented as an absolute truth. A reasonable initial heuristic is:

1. Evaluate 15, 30, 60, and 120 minutes.
2. Reject or warn on a timeout if it models more than a configurable number of completed spin-ups per day, default `4`.
3. Among remaining choices, prefer the shortest timeout that gains at least a configurable share, default `85%`, of the maximum spun-down hours achieved by any tested timeout.
4. Require at least 72 hours of valid observation before showing a confident recommendation.
5. Before 72 hours, label results **Preliminary**.

Always show the underlying table so the owner can choose differently. Allow a different selection per disk because parity, recently added media, and frequently watched media disks can have very different workloads.

---

## 7. Web UI requirements

The UI is served by the container on port `8080` by default. It is intended for local LAN or VPN access. No authentication is required for MVP; warn users not to expose it directly to the public Internet.

### Main dashboard

Top summary cards:

- collection status and uptime
- valid observation duration
- discovered/enabled disks
- disks currently idle longer than 30 minutes
- current preliminary/recommended array timeout

Per-disk table, sortable by any column:

| Column | Meaning |
| --- | --- |
| Disk | Current `sdX` name |
| Model | Model/serial when available |
| State | Active recently or idle |
| Current idle | Time since last observed I/O |
| Last activity | Local timestamp |
| Longest idle | Longest valid completed interval |
| Median idle | Median valid completed interval |
| 30m cycles/day | Modeled completed spin-ups/day at 30 minutes |
| 60m cycles/day | Modeled completed spin-ups/day at 60 minutes |
| Recommendation | Preliminary or recommended timeout |

Use clear color sparingly:

- green: long stable idle / good candidate
- amber: incomplete observation or moderate cycling
- red: high modeled cycling or collector problem
- grey: disabled, missing, or insufficient data

### Disk detail page

Include:

1. Current idle duration and last I/O deltas.
2. Summary statistics: count, longest, median, p75, p90 idle interval.
3. Timeout comparison table for 15/30/60/120 minutes and custom values.
4. Bar chart: modeled spun-down hours/day by timeout.
5. Line or bar chart: modeled spin-ups/day by timeout.
6. Idle-duration histogram using useful buckets such as `<5m`, `5–15m`, `15–30m`, `30–60m`, `1–2h`, `2–4h`, `4–8h`, and `8h+`.
7. Recent activity/idle event list.
8. Explanation of censored or excluded periods.

### Array analysis page

Show an aggregate table for each timeout:

- sum of modeled completed spin-ups/day across enabled disks
- sum of modeled spun-down disk-hours/day
- number of disks benefiting from the timeout
- number of disks exceeding the cycling warning threshold

Do not average idle duration across drives as the primary array metric; disk-hours and cycle totals are more meaningful.

### Settings page

Allow:

- sample interval, minimum 10 seconds and default 30 seconds
- tested timeout list
- cycling warning threshold
- recommendation efficiency threshold
- include/exclude regex
- enabled/disabled disks
- timezone, default inherited from `TZ`
- clear data action with a confirmation step

### Data export

Provide:

- `idle-intervals.csv`
- `activity-events.csv`
- `timeout-analysis.csv`
- complete JSON export including settings and metadata

Exports should stream responses and must not stop collection.

---

## 8. HTTP endpoints

Suggested endpoints:

```text
GET  /                         Main dashboard HTML
GET  /disks/{device_name}      Disk detail HTML
GET  /analysis                 Array analysis HTML
GET  /settings                 Settings HTML

GET  /api/health               Liveness/readiness information
GET  /api/status               Collector and observation summary
GET  /api/disks                Current disk states and summaries
GET  /api/disks/{name}         Detailed disk statistics
GET  /api/disks/{name}/events  Paginated recent events
GET  /api/analysis             Timeout simulation for all disks
GET  /api/settings             Current settings
PUT  /api/settings             Validate and update settings
GET  /api/export/{kind}        CSV or JSON export
POST /api/data/clear           Confirmed destructive history reset
```

`/api/health` should return non-200 if the collector has not successfully parsed diskstats for more than three sample intervals. Docker should use this endpoint for health checks.

---

## 9. Configuration

Support these environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8080` | Web server port inside container |
| `TZ` | `UTC` | Display timezone |
| `DATA_DIR` | `/data` | SQLite and configuration directory |
| `DISKSTATS_PATH` | `/host/proc/diskstats` | Host diskstats source |
| `SYS_BLOCK_PATH` | `/host/sys/block` | Optional host sysfs block-device path |
| `SAMPLE_INTERVAL_SECONDS` | `30` | Initial collection cadence |
| `INCLUDE_DEVICE_REGEX` | `^sd[a-z]+$` | Default whole-disk inclusion rule |
| `EXCLUDE_DEVICE_REGEX` | empty | Additional exclusion rule |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

Settings changed in the web UI should persist in `/data` and override environment defaults after first start, except for paths and server port, which remain deployment configuration.

---

## 10. Docker requirements

The image should:

- use a small pinned Python base image
- run as a non-root user
- expose port 8080
- store all writable state under `/data`
- include a Docker health check against `/api/health`
- handle `SIGTERM` cleanly, stop collection, checkpoint state, and close SQLite
- not request privileged mode or extra Linux capabilities
- be buildable for `linux/amd64`; `linux/arm64` support is welcome but not required for the owner's Unraid server

Example compose file for development or non-Unraid use:

```yaml
services:
  hdd-idle-profiler:
    image: ghcr.io/OWNER/unraid-hdd-idle-profiler:latest
    container_name: hdd-idle-profiler
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      TZ: America/Toronto
    volumes:
      - ./data:/data
      - /proc/diskstats:/host/proc/diskstats:ro
      - /sys/block:/host/sys/block:ro
```

Verify on Unraid that the read-only file bind for `/proc/diskstats` works as expected. If Docker exposes the same host-wide counters directly inside the container, keep `DISKSTATS_PATH` configurable but retain the explicit bind in documentation because it makes the data source unambiguous.

---

## 11. Unraid template requirements

Create `unraid/unraid-hdd-idle-profiler.xml` and host its raw GitHub URL from the owner's repository. The template should be compatible with Unraid's Docker template system and suitable for later Community Applications submission.

Template values:

| Setting | Value |
| --- | --- |
| Name | `HDD-Idle-Profiler` |
| Repository | `ghcr.io/OWNER/unraid-hdd-idle-profiler:latest` |
| Network | `bridge` |
| Web UI | `http://[IP]:[PORT:8080]/` |
| Container port | `8080` |
| Default host port | `8080` |
| Appdata host path | `/mnt/user/appdata/hdd-idle-profiler` |
| Appdata container path | `/data` |
| Diskstats host path | `/proc/diskstats` |
| Diskstats container path | `/host/proc/diskstats` read-only |
| Sysfs host path | `/sys/block` |
| Sysfs container path | `/host/sys/block` read-only |
| Timezone | `America/Toronto` or inherit an existing Unraid convention |
| Privileged | `false` |

Include template fields for sample interval and device regexes as advanced settings. The template description must explicitly say that the app observes I/O and estimates timeout effects; it does not spin disks down or alter Unraid configuration.

Repository documentation must explain both installation methods:

1. Add the raw XML URL through Unraid's Docker template workflow for personal use.
2. Pull/run the Docker image manually.

Replace every `OWNER` placeholder once the GitHub repository and image namespace are known.

---

## 12. Accuracy and UX details

- Display browser-local or configured-timezone timestamps consistently.
- Use monotonic time for in-process elapsed calculations, but persisted UTC timestamps for history.
- Clearly distinguish **observed idle**, **modeled spun down**, and **actual drive standby**. The MVP knows the first, estimates the second, and does not claim the third.
- Round current durations for display but retain precise timestamps in calculations.
- Use median and percentiles instead of only averages because idle gaps will likely be heavily skewed.
- A sample interval means activity timing is known only within that interval. Tests and wording should acknowledge this resolution.
- If no counters change during a sample, do not write a database activity row.
- If multiple counter changes occur within one polling window, treat them as one observed activity event with aggregated deltas.
- SQLite should use WAL mode, foreign keys, indexes on disk/timestamp, and periodic safe checkpoints.
- The UI must work comfortably on desktop and phone screens.

---

## 13. Testing requirements

### Parser tests

- Parse representative modern `/proc/diskstats` rows.
- Ignore malformed rows without crashing the collector.
- Handle optional discard/flush fields.
- Correctly distinguish whole devices from partitions.

### Collector tests

- No event when counters are unchanged.
- Correct deltas when reads/writes increase.
- Counter decrease creates a reset/discontinuity rather than negative I/O.
- Appearing and disappearing devices are handled.
- Restart restores valid baselines or starts a new observation segment safely.
- Collector does not open a block device path.

### Analysis tests

For a 3-hour completed idle gap:

| Timeout | Modeled down duration | Completed spin-ups |
| ---: | ---: | ---: |
| 15 min | 2 h 45 min | 1 |
| 30 min | 2 h 30 min | 1 |
| 60 min | 2 h | 1 |
| 120 min | 1 h | 1 |

Also test:

- gap equal to timeout does not produce a modeled cycle
- open interval does not count a completed spin-up
- discontinuous/censored gaps are excluded appropriately
- daily normalization uses valid observed duration
- median and percentiles are correct
- recommendation remains preliminary before 72 valid hours

### API/UI tests

- health endpoint reflects stale collector state
- empty/new installation renders useful zero-state pages
- 20+ disks render and sort correctly
- invalid regex/settings are rejected with a useful message
- exports contain consistent calculations
- clear-history action requires explicit confirmation

### Container acceptance test

On an Unraid host:

1. Install from the XML template.
2. Confirm the container starts without privileged mode.
3. Confirm `/api/health` is healthy.
4. Confirm expected `sdX` disks appear.
5. Confirm the database is created under appdata.
6. Confirm container restart retains history.
7. Confirm disabling a disk removes it from aggregate recommendations without deleting its history.
8. Confirm the collector produces no read activity on otherwise idle array disks.

---

## 14. Definition of done for MVP

The MVP is complete when:

- The container installs on Unraid from the hosted XML template.
- The app collects host diskstats safely for all selected array HDDs.
- The browser dashboard shows live idle times and remains usable with more than 20 drives.
- At least 15/30/60/120-minute simulations are accurate and covered by tests.
- Per-disk and array-level pages expose modeled spin-ups/day and spun-down hours/day.
- Collection history survives container updates/restarts through the appdata mapping.
- CSV/JSON exports work.
- README documents installation, interpretation, limitations, and security.
- No app feature spins up, spins down, or otherwise controls a drive.
- All tests pass in the repository's CI workflow.

---

## 15. Suggested implementation sequence for Codex

1. Scaffold the repository and tests.
2. Implement and fixture-test the diskstats parser.
3. Implement discovery/filtering and the in-memory sampling state machine.
4. Add SQLite schema, migrations, and event persistence.
5. Implement idle-interval and timeout-analysis functions with deterministic unit tests.
6. Build JSON endpoints and health checks.
7. Build the responsive dashboard, disk page, analysis page, and settings page.
8. Add exports.
9. Build and run the Docker image locally against fixture data, then host counters.
10. Add the Unraid XML template and complete README instructions.
11. Test on the owner's Unraid server and adjust host mounts/device filtering.
12. Publish the image to GHCR and replace repository placeholders.

Codex should implement the app in small, testable slices. Do not begin by adding SMART or Unraid-control functionality; the first release should remain a trustworthy, non-invasive profiler.

---

## 16. Initial README warning text

> HDD Idle Profiler observes Linux block-device I/O counters and models what would happen under different spin-down timeouts. It does not read your files, issue drive power commands, change Unraid settings, or verify actual platter power state. Treat its results as workload guidance, not a guarantee of drive behavior. Keep the web interface on a trusted LAN or VPN and do not expose it directly to the public Internet.

---

## 17. Decisions intentionally left for the build session

These items require the future repository context or owner input:

- Final app/repository name.
- GitHub username/organization and GHCR image path.
- License choice.
- App icon and screenshot URLs used by the Unraid template.
- Whether the first release uses Chart.js or pure HTML/CSS for every visualization.
- Whether Unraid's runtime exposes enough model/rotational metadata through the proposed `/sys/block` read-only mount.
- Whether the owner wants a single array-wide recommendation, per-disk recommendations, or both applied manually in Unraid. The UI should support both analyses even though the app applies neither.

