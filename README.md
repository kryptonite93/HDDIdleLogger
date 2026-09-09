# HDD Idle Profiler

Optional [likely source capture](unraid/REQUEST-CAPTURE.md) identifies original container/process requests near resumed disk I/O and shows them in the main disk table. It requires additional container permissions and mounts and remains off by default. The old physical-disk tracer that reported `mdunraidd` has been removed; its source records remain exportable. The replacement reports likely, multiple or unknown sources, not proven physical spin-ups.

A personal Unraid workload profiler. Observe physical-disk I/O for several days, then compare 15, 30, 60 and 120 minute spin-down delays before changing anything in Unraid.

> HDD Idle Profiler observes Linux block-device I/O counters and models what would happen under different spin-down timeouts. It does not read your files, issue drive power commands, change Unraid settings, or verify actual platter power state. Treat its results as workload guidance, not a guarantee of drive behavior. Keep the web interface on a trusted LAN or VPN and do not expose it directly to the public Internet.

## Current status

The implementation includes the collector, SQLite persistence, timeout analysis, responsive UI, settings, CSV/JSON exports, optional request capture, tests, Docker files and Unraid XML templates. [GitHub Actions](https://github.com/kryptonite93/HDDIdleLogger/actions) runs the test suite and container smoke checks before publishing `ghcr.io/kryptonite93/hddidlelogger:latest`. Request tracing identified Plex on disk10, independently confirmed by the owner. The new counter correlation and directory filtering still require live Unraid acceptance.

The owner requested personal use and has not chosen a license. No public reuse license is assigned in this repository. Upstream Python dependencies retain their own licenses. Community Applications submission, icon and support-thread assets are deferred.

## Quick template installation on your server

For your Unraid 7.3.1 server with the `cache` pool, run this in the Unraid terminal:

```sh
mkdir -p /mnt/cache/appdata/hdd-idle-profiler
chown 99:100 /mnt/cache/appdata/hdd-idle-profiler
chmod 750 /mnt/cache/appdata/hdd-idle-profiler
curl -fL https://raw.githubusercontent.com/kryptonite93/HDDIdleLogger/main/unraid/unraid-hdd-idle-profiler.xml \
  -o /boot/config/plugins/dockerMan/templates-user/my-hdd-idle-profiler.xml
```

Then open **Docker → Add Container**, select **HDD-Idle-Profiler**, check the port and cache path, and click **Apply**. Unraid downloads the prebuilt public image; it does not compile the source. Open its WebUI and check the discovered disks. This is a personal template, so it will not appear in Community Applications search yet. On a later reinstall, preserve an existing customized personal XML before downloading over it.

## Start locally

Python 3.12 is required. No Node build or CDN is used.

```sh
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# PowerShell alternative: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest -q
```

For a labeled 24-disk synthetic preview:

```sh
python scripts/demo.py
```

Open [the local preview](http://127.0.0.1:8080). The preview reads only generated fixture data under `data/synthetic-preview`, resets that synthetic history on each launch, and binds to loopback. Stop it with Ctrl+C. The production image does not include the demo script.

For a new empty installation using a static fixture in PowerShell:

```powershell
$env:DATA_DIR = "$PWD/data/local"
$env:DISKSTATS_PATH = "$PWD/tests/fixtures/diskstats"
$env:SYS_BLOCK_PATH = "$PWD/tests/fixtures/sys"
.venv/Scripts/python.exe -m app.main
```

On Linux, use the corresponding environment variables before `python -m app.main`. Production binds to `0.0.0.0` on port 8080; use trusted networking. Run **one** Uvicorn worker and **one** container against a given data directory. Multiple collectors sharing one database are unsupported.

## Build locally on Unraid (optional)

Your confirmed target is **Unraid 7.3.1**, with the pool named **`cache`**. The commands below already use your appdata path: `/mnt/cache/appdata/hdd-idle-profiler`.

Copy the project source to a directory on your Unraid cache/pool, open the Unraid terminal, and change to that directory. Use your actual pool name in place of `cache`. Do not use a pool-backed share that Mover can transfer to the monitored array.

```sh
# Run from the copied project directory.
docker build -t hdd-idle-profiler:local .
mkdir -p /mnt/cache/appdata/hdd-idle-profiler
chown 99:100 /mnt/cache/appdata/hdd-idle-profiler
chmod 750 /mnt/cache/appdata/hdd-idle-profiler
docker run -d --name hdd-idle-profiler \
  --restart unless-stopped --user 99:100 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -p 8080:8080 -e TZ=America/Toronto \
  --mount type=bind,src=/mnt/cache/appdata/hdd-idle-profiler,dst=/data \
  --mount type=bind,src=/proc/diskstats,dst=/host/proc/diskstats,readonly \
  --mount type=bind,src=/sys,dst=/host/sys,readonly \
  hdd-idle-profiler:local
```

Open `http://YOUR-UNRAID-IP:8080`. If port 8080 is in use, change the host side to `-p 8081:8080`. Initial image construction downloads dependencies; operation is fully offline.

The image defaults to UID/GID 1000:1000. The Unraid template and compose file override this with Unraid's conventional 99:100. On another Linux host, use an appropriate unprivileged UID/GID and ensure the bind-mounted directory is writable by that user. Ownership preparation is needed because a non-root container cannot repair a root-owned bind mount.

Alternatively, after preparing appdata, run `docker compose up -d --build`. Compose uses `/mnt/cache/appdata/hdd-idle-profiler` by default; set `APPDATA_PATH` to change it. It builds locally even before GHCR publication.

### Why the sysfs mount differs from the handoff

`/sys/block/sdX` commonly points into `../devices/...`. Mounting only `/sys/block` can leave those relative links dangling. The supplied deployment mounts `/sys` at `/host/sys`, read-only, so the app can read `/host/sys/block/sdX/queue/rotational` and optional model/serial/vendor fields. The app never scans other sysfs trees. You can omit the mount, but SSD filtering then has no rotational evidence; verify the selected devices manually. Missing metadata never stops collection.

**Put `/data` on cache/pool storage.** Kernel-counter reads are designed not to wake disks, but SQLite checkpoint and event writes are real storage I/O. Appdata or Docker storage on a monitored array disk can contaminate the observation. This cannot be detected reliably from inside the container.

## Install from the Unraid template

The raw template URL is:

```text
https://raw.githubusercontent.com/kryptonite93/HDDIdleLogger/main/unraid/unraid-hdd-idle-profiler.xml
```

After the repository and image are available, download the raw XML as a personal Docker template and use **Docker → Add Container → Template → HDD-Idle-Profiler**. The exact UI wording can vary by Unraid release. The documented personal template directory is `/boot/config/plugins/dockerMan/templates-user`; see [Unraid's Community Applications documentation](https://docs.unraid.net/unraid-os/manual/applications/).

```sh
curl -fL https://raw.githubusercontent.com/kryptonite93/HDDIdleLogger/main/unraid/unraid-hdd-idle-profiler.xml \
  -o /boot/config/plugins/dockerMan/templates-user/my-hdd-idle-profiler.xml
```

Prepare the appdata permissions above, choose the template, inspect its pool path and port, then install. A private repository requires an authenticated download; a private GHCR image requires Docker registry login on the server. No credentials belong in the template. Do not overwrite an existing personal template containing customized settings without preserving it first.

For manual image installation after publication, replace `hdd-idle-profiler:local` in the earlier run command with `ghcr.io/kryptonite93/hddidlelogger:latest`. Updates retain all observations as long as `/data` points to the same directory. A pinned commit image tag is also published by the manual workflow for reproducible installs.

## Reading the results

- **Current observed idle** ends at the last successful sample, so a failed collector never creates growing fictional idle time. `≥` marks an initial interval whose earlier activity is unknown.
- **Modeled down hours** estimate what a timer could have achieved; they do not measure actual standby or energy use. An I/O request may be served by a drive's cache, and delayed writeback may separate application access from block I/O.
- For a completed gap `G` and timer `T`, `G > T` produces one modeled completed spin-up and `max(0, G − T)` down seconds. Equality does not count. A three-hour gap yields 2.75, 2.5, 2 and 1 down hours for the standard timers.
- Open intervals add clearly labeled current potential down time and no completed spin-up. A known idle interval terminated by collection ending retains only its proven portion; its CSV `end_is_censored=1` flag means no resumption was observed. Median, percentiles, histogram and completed-cycle counts exclude unfinished tails. Longest observed includes ongoing and partial quiet periods, marked ≥, and stops at the last successful sample.
- Initial intervals with unknown beginnings are excluded from medians, percentiles and modeled savings. An always-idle disk can therefore show a long `≥` duration but no suggestion until enough known intervals exist.
- Restart, source error, missing disk, counter decrease, host boot change, clock adjustment, selection change or an excessive polling delay ends the observation at its last good sample. New observation starts with a fresh baseline. Downtime never bridges segments.
- Valid time is the sum of sampled segment lengths, including known observation time before the first I/O. Daily rates divide by that disk's valid time, not wall time since installation. Array rates sum per-disk rates; they are not an average idle duration or necessarily the exact total on one shared calendar day.
- Recommendations reject timers above the configured completed-cycle limit (default 4/day), then choose the shortest timer retaining at least 85% of the maximum modeled down time. The two constraints can conflict: the app then shows no suggestion. Results remain preliminary below 72 valid hours per disk; the array stays preliminary until every included disk qualifies.
- Median quiet, percentiles and histogram use completed periods containing at least one poll with unchanged counters. Consecutive active readings remain in raw history and timeout modeling but are excluded from these quiet statistics. Custom timers shorter than the sample interval have particularly weak resolution.
- Upgrading preserves all history and settings. Schema version 2 adds nullable `quiet_sample_observed`: 1 means a quiet poll was observed, 0 means none was observed, and null identifies older history. For older history, quiet statistics estimate qualifying gaps as at least 1.5 times that session's sampling interval, allowing ordinary polling jitter. Delayed old polls can still be misclassified; the UI labels these estimates. No history reset is needed.
- After a restart, state shows “Waiting for next reading” until two readings can be compared. Unchanged counters then show “Idle”; changed counters show “Active recently.” Current idle restarts from the new baseline because collection downtime is unknown; earlier longest observations remain in history as partial observations.

Current device names are the identity for this MVP. Serial/model fields are for display. Replacing hardware under the same name or renumbering drives across boots can mix historical identity; export then clear history for a new profiling run. Disabled and missing disks keep history but do not contribute to array analysis.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8080` | Container HTTP port |
| `DATA_DIR` | `/data` | SQLite directory |
| `DISKSTATS_PATH` | `/host/proc/diskstats` | Read-only kernel-counter source |
| `SYS_BLOCK_PATH` | `/host/sys/block` | Optional device metadata |
| `BOOT_ID_PATH` | `/proc/sys/kernel/random/boot_id` | Boot-boundary source, override for fixtures |
| `TZ` | `UTC` | Initial IANA display timezone |
| `SAMPLE_INTERVAL_SECONDS` | `30` | Initial cadence, 10–3600 seconds |
| `INCLUDE_DEVICE_REGEX` | `^sd[a-z]+$` | Initial inclusion filter |
| `EXCLUDE_DEVICE_REGEX` | empty | Initial exclusion filter |
| `LOG_LEVEL` | `INFO` | Console log level |

UI settings persist in SQLite and override environment defaults after first start; port and paths remain deployment configuration. Regex filters apply to physical whole-disk candidates only, never partitions, NVMe or virtual devices. The default includes `sd[a-z]+`; extended `hd`, `vd` and `xvd` names require an explicit matching regex and rotational metadata must not report an SSD. A settings save takes effect at the next poll. The selected list updates as discovery catches up without overwriting unsaved checkbox choices.

The application does not enable CORS or authentication. State-changing endpoints require JSON and reject cross-origin browser requests. These checks do not make public exposure safe. The API schema is at `/openapi.json`; CDN-backed documentation pages are disabled so the app uses no external browser assets.

## Storage and exports

SQLite uses WAL, foreign keys, disk/timestamp indexes, transactions and periodic passive checkpoints. Idle samples update one checkpoint per live segment instead of inserting activity rows. Changed counters generate a delta event and a completed interval. Busy disks can still produce an event on every sample. There is no automatic retention policy; export and clear history between separate profiling runs.

CSV exports: `/api/export/idle-intervals.csv`, `/api/export/activity-events.csv`, `/api/export/timeout-analysis.csv`. Full snapshot: `/api/export/complete.json`. UTC Unix-second timestamps retain fractional precision. Counter-derived sectors always represent **512 bytes**, independently of physical sector size. The counter definitions follow the [Linux kernel I/O statistics documentation](https://www.kernel.org/doc/html/latest/admin-guide/iostats.html).

Raw exports iterate a separate database read snapshot and do not hold the collector lock. Long-running downloads can grow the WAL until the reader completes. Download JSON for portable data inspection; it is not an automatic restore format. To back up the database file directly, stop the container first or use SQLite's online backup API rather than copying only the main file while WAL writes are active.

`POST /api/data/clear` requires `{"confirmation":"CLEAR HISTORY"}`. It removes observation history and keeps settings/selection. The next sample establishes new baselines. UI clearing is deliberately confirmed by typing the same phrase.

## Acceptance checklist on your Unraid host

Record the Unraid version and actual pool path with the test results.

1. Build and start with the command above (or install the published template). Confirm `docker inspect -f '{{.HostConfig.Privileged}} {{.Config.User}}' hdd-idle-profiler` reports `false 99:100`.
2. Open the dashboard and `/api/health`. A successful parse makes health return HTTP 200; after more than three sample intervals without success it returns 503. Docker adds its own health-check retries.
3. Confirm expected array/parity physical `sdX` devices are present. Compare model/serial and rotational status with Unraid. Disable cache SSDs or unrelated disks; unknown metadata requires manual verification.
4. Confirm `profiler.sqlite3` and its WAL files exist under the chosen pool appdata directory and that the UI shows increasing valid observation after successive polls.
5. Let ordinary server use produce activity, export JSON, restart the container, and confirm previous events remain. The restart is an explicit observation boundary, not extra idle time.
6. Disable one disk, save, and confirm it leaves array totals while its detail/history remains available. Re-enable it and wait one sample for a new baseline.
7. With scheduled parity checks, scans and ordinary workload accounted for, use existing Unraid observations to compare counter changes on an otherwise idle disk before and during collection. Do not use reads or SMART probes to test idleness. The app cannot by itself prove physical power behavior.
8. Verify each CSV and JSON download, a custom timer, settings persistence, and mobile access. Do not clear valuable history just to test clearing; use a disposable appdata directory.
9. Run for several days and inspect censored intervals and collection events before trusting normalized daily estimates.

The container build smoke test can be run with `docker build -t hdd-idle-profiler:test .` followed by `sh scripts/container-smoke.sh`; it uses only static fixtures and cleans up its own test container. It verifies non-root operation, discovery and restart persistence, not real host behavior.

## Development and publication

`python -m pytest -q` exercises parsing, discovery, reset/restart/downtime boundaries, non-device reads, timeout arithmetic, normalization, confidence, settings, exports and API safety. `scripts/browser-check.cjs` uses Playwright plus installed Chrome against the synthetic preview to exercise 24 disks, sorting, polling focus, settings persistence and phone layouts. Set `PLAYWRIGHT_MODULE` if Playwright is outside the usual Node module path. Screenshots are written to ignored `artifacts/`.

CI runs pytest, builds the Docker image and runs a fixture container smoke test. The **Publish image** workflow runs on pushes to `main` or manual dispatch, repeats the tests and container smoke check, then publishes `latest` and a commit tag to GHCR only after success. It requires repository Actions permissions to write packages. The initial build and publication succeeded on 2026-09-07. Public repository and package visibility were requested by the owner.

The pinned Python image tag is listed in the [official Python image registry](https://hub.docker.com/_/python/tags?name=3.12.14-slim-bookworm). Runtime package pins were validated locally; Docker/Linux verification belongs to CI and the owner-run acceptance test.
