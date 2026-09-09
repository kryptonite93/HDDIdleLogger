# Enable likely source capture

Optional request capture runs inside HDD Idle Profiler. It puts a likely container/process in the main disk table when physical disk counters resume after at least 60 seconds of quiet. It does not measure standby or prove that a request caused a physical spin-up. The old `mdunraidd` tracer remains removed.

## Update your existing Unraid container

1. Update the HDD-Idle-Profiler image, then choose **Edit** and enable **Advanced View**. Keep your existing appdata and Web UI port.
2. Set **Extra Parameters** to:

   ```text
   --user=0:0 --cap-drop=ALL --cap-add=DAC_OVERRIDE --security-opt=no-new-privileges:true --stop-timeout=25
   ```

   Keep **Privileged** off. Add a **Variable** named `REQUEST_ATTRIBUTION_ENABLED` with key `REQUEST_ATTRIBUTION_ENABLED` and value `true`. The old `ATTRIBUTION_ENABLED` variable has no effect.
3. Ensure these host paths map to the container paths shown. Keep the existing `/data` and `/host/sys` mappings.

   | Host path | Container path | Access |
   |---|---|---|
   | `/proc/diskstats` | `/host/proc/diskstats` | Read Only |
   | `/sys/kernel/debug/tracing` | `/host/tracing` | Read/Write |
   | `/proc` | `/host/processes` | Read Only |
   | `/usr/lib64/libfuse3.so.3.16.2` | `/host/libfuse.so` | Read Only |
   | `/var/local/emhttp` | `/host/unraid` | Read Only |
   | `/var/lib/docker/containers` | `/host/docker-containers` | Read Only |

4. Apply, open the profiler and check the capture status below the disk table. Once it says it is matching requests, allow the target disk at least **60 seconds without I/O**, then play a previously unopened file on that disk. Allow several seconds for matching and the next page refresh. The latest likely source should show `plex` when its request is the only matching source.

The library path matches the inspected Unraid 7.3.1 / 6.18.33 x86-64 host. Kernel/library changes may require another compatibility check. No Docker socket, host Python installation, array-file mount or separate long-running helper is needed. The optional Docker metadata mount provides names; without it, container identities appear as shortened IDs in the UI.

For a fresh install, the optional [request-capture template](unraid-hdd-idle-profiler-request-capture.xml) includes these settings. Do not run a second instance against the same appdata directory. Compose users can use:

```sh
docker compose -f docker-compose.yml -f docker-compose.request-capture.yml up -d
```

## What the result means

- **File request + resumed I/O:** one original requester matched a successful backing-file open on that array disk within ten seconds before to two seconds after counters resumed. The requester is scoped to its FUSE callback; the connection to physical I/O is a time correlation, not an end-to-end causal proof.
- **Multiple sources:** several requesters matched that window. Retained candidates are shown; no arbitrary winner is selected.
- **Unknown source:** I/O resumed but no supported request matched. A previous source is not reused for the new event.
- **No resumed-I/O record:** no qualifying event has been recorded, or capture is off. Check the status below the table.

Directory-flagged opens, `O_PATH` opens and array mount-root opens are excluded. A cached open without a counter change produces no record. Already-open descriptors, direct `/mnt/diskN` bypasses, delayed writeback, metadata-only operations and parity activity are not reliably attributed. Background file requests may coincidentally match unrelated I/O. This feature is opt-in and these limits remain visible in the UI.

## Lifecycle and storage

The child process owns a unique trace instance and probe group and cleans them on normal shutdown or errors. Mapping/worker changes and one-hour session rollover start a fresh baseline. Sampling gaps, invalid correlations and counter resets discard pending evidence. After each restart, another quiet period is required. Capture errors do not stop ordinary idle profiling; the UI reports the capture error and retries after ten seconds. Failed cleanup stops automatic retries, so inspect the reported `hddproof_...` group in container logs before restarting. Never clear all system probes.

Capture reads `/proc/diskstats` once per second and cached Unraid mapping data every ten seconds. It never issues a drive command. Keep appdata on cache/pool storage. New source records retain up to 30 days as new events arrive; old retired records remain exportable. Database version 4 adds an evidence column without deleting observations or settings. Clearing history restarts active request capture with a fresh baseline.

Disabling `REQUEST_ATTRIBUTION_ENABLED` stops the new capture on container restart and preserves recorded history. If returning to UID 99, ensure this app's appdata files are writable by UID 99/GID 100 after stopping the container.

The [investigation notes](ATTRIBUTION.md) contain the independently confirmed Plex-to-disk10 proof. The new counter correlation and directory filtering still require acceptance on the live server; local tests exercise ambiguity, cached reads, simultaneous disks, resets, migration, and API presentation.
