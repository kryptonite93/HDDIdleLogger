# Validation record

Local validation on Windows with Python 3.12, 2026-09-07:

- Pytest covers parser/filtering, counter deltas, unchanged checkpoints, resets, disappearance, restart boundaries, failed polls, monotonic/wall-clock discontinuities, optional metadata, SSD exclusion, safe reads, timeout arithmetic, censoring, percentiles, recommendation confidence, daily/array normalization, API validation, exports, selection persistence and history clearing.
- Playwright with installed headless Chrome against a clearly labeled synthetic 24-disk preview covers dashboard sorting, disk details, array analysis, settings persistence, retained keyboard focus across polling, dynamic eligibility without losing unsaved selections, no page overflow at 390px, and no JavaScript errors. Desktop and mobile screenshots are in ignored `artifacts/`.
- Export generator verification switches between separate worker threads while collection writes to SQLite; streaming uses a dedicated sequential connection with WAL.
- Unraid template XML is parse-checked locally. Container build and fixture smoke test are configured in GitHub Actions but have not run here. Docker is not installed on this host.

## Independent UI review: final verdict

| Finding / area | Final verdict |
| --- | --- |
| Disk-link focus lost during refresh | Resolved |
| Stale disk eligibility after filter changes | Resolved |
| Desktop/mobile presentation | Pass |
| UI finish review | Pass |
| Real Unraid acceptance | Pending owner validation |

The reviewer inspected the code and all eight saved desktop/mobile screenshots. Browser execution was performed by the build agent. The visual detector's navigation underline warning was a false positive; short dashboard copy was a non-blocking tone observation.

## Not yet verified

Owner-confirmed target: Unraid 7.3.1, `cache` pool, appdata at `/mnt/cache/appdata/hdd-idle-profiler`. These configuration details are confirmed; host test results are still pending.

- Docker image builds/runs on Linux and is healthy without privileges.
- Actual Unraid diskstats and sysfs mounts, model/serial visibility, appdata ownership and disk selection.
- Non-waking behavior on otherwise idle host disks, accounting for Docker/appdata storage and other workloads.
- Several days of real workload, hardware replacement or device renumbering behavior.
- GitHub repository/image publication and installation from a hosted XML template.

Use the owner-run checklist in README.md. This record is not a claim of completed host acceptance.
