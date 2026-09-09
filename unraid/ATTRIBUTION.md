# Optional activity source capture

The profiler can capture the first block request after a quiet period and show its issuing process and Docker container, when identifiable. Open a disk to see **Likely activity sources**. This captures future activity only; it cannot explain old records or prove that a disk physically spun up.

## Enable on the existing Unraid container

The supplied Unraid 7.3.1 / kernel 6.18.33 capability report includes isolated trace instances and the required `block_bio_queue` event. Parsing and application behavior are tested; the mount permissions and real capture still require acceptance on your host.

1. Update HDD-Idle-Profiler to the latest image. Edit its configuration and enable Advanced View.
2. Replace Extra Parameters with:

   ```text
   --user=0:0 --cap-drop=ALL --cap-add=DAC_OVERRIDE --security-opt=no-new-privileges:true
   ```

   Leave **Privileged off**. Optional capture runs as root inside this container. DAC_OVERRIDE allows reading protected host process and Docker metadata and accessing existing appdata. No SYS_ADMIN capability, host PID namespace, Docker socket, host network, or block-device mount is requested.

3. Add these entries using **Add another Path, Port, Variable, Label or Device**:

   | Type | Name | Host path / value | Container path / key | Access |
   |---|---|---|---|---|
   | Variable | Enable activity sources | `true` | `ATTRIBUTION_ENABLED` | — |
   | Path | Kernel tracing | `/sys/kernel/debug/tracing` | `/host/tracing` | Read/Write |
   | Path | Host processes | `/proc` | `/host/processes` | Read Only |
   | Path | Docker names (optional) | `/var/lib/docker/containers` | `/host/docker-containers` | Read Only |

   Keep existing appdata, diskstats and sysfs mounts. To confirm Docker's metadata location, run `docker info --format '{{.DockerRootDir}}'` in the Unraid terminal and append `/containers`. Without that optional mount, container IDs can still be shown. The metadata directory also contains Docker configuration and logs: this grants the container read access to that directory, although the capture code reads only `config.v2.json` and retains only ID and name. A read-only Docker socket would still allow Docker control, so it is deliberately not used.

4. Apply the configuration. Open **Settings → Activity source capture**, or open a disk. The status should say **Capturing**. If it says **unavailable**, send the displayed error; don't enable full privileged mode to work around it.
5. Let normal workload run. The first captured request per disk has an unknown preceding quiet duration. Subsequent records are saved after at least 60 seconds without a traced request on that disk. Optionally set `ATTRIBUTION_IDLE_SECONDS` to another value from 10 to 86400 seconds. This is a logging threshold, not a drive spin-down timer.

## How to interpret the records

- **Issuing process matched to container:** the process's cgroup identified that container at capture time. This is evidence of the block request issuer, not proof of a physical wake or the ultimate application responsible.
- **Host or kernel issuer:** for example `shfs`, `smbd`, a parity worker or `kworker`. Unraid's user-share layer and buffered/background writes can obscure the original container. The profiler does not infer a container from coincident activity.
- **Process exited or could not be resolved:** the process was gone, changed identity, or could not be read before resolution. The recorded trace command remains visible. IDs are not guessed from process names.
- **Unknown · capture boundary:** the first request after startup, selection changes, trace loss or clock discontinuity. No quiet interval is claimed across that boundary.
- **Trace loss:** the UI reports lost events. Capture history can be incomplete under heavy load; it cannot be treated as an exhaustive audit.

This version captures physical-disk `block_bio_queue` events, filtered to selected HDDs. It does not follow file paths or reconstruct FUSE, device-mapper, parity or asynchronous writeback causality. SMART/power commands that do not produce these events are outside its coverage. No old activity is backfilled.

## Storage, shutdown and disabling

Records are stored in the existing appdata database on cache/pool storage. Only the latest 5,000 source records are retained globally. Export them from Settings as CSV or in Complete JSON. Clearing history also clears source records. Existing idle history and timer models are unchanged.

The tracer creates a unique `hddidle-*` instance, uses a 64 KiB buffer per CPU, and disables/removes only that instance on orderly shutdown. A free-buffer descriptor with `disable_on_free` stops recording if the process exits unexpectedly; an empty instance directory may remain after a forced kill. It never enables global tracing or changes another trace session. Tracing adds overhead; use it for diagnosis and disable it when finished.

Set `ATTRIBUTION_ENABLED=false` and restart to disable capture; saved records remain. You may remove the three extra mounts. If you also revert the container to UID 99/GID 100, stop it first and restore ownership of this app's appdata directory to 99:100 from Unraid, because root mode can create root-owned database sidecar files. Do not clear or replace the database.

## Acceptance check

Confirm normal profiler collection stays healthy and attribution says Capturing. After a disk has had at least one captured request and then 60 seconds without I/O, perform an ordinary operation from a known container that uses that disk. Check its source row and compare the process/container against the operation you performed. A host/kernel issuer is an honest limitation, not a successful container match. Restart once and verify old rows remain and the next captured request has unknown quiet duration. Disable capture and confirm no new source rows appear while normal profiling continues.

Reference: [Linux ftrace documentation](https://docs.kernel.org/trace/ftrace.html), particularly trace instances, event filters, `trace_pipe`, and `free_buffer`.
