# Replacing physical-disk attribution

## Current status

The old `block_bio_queue` tracer has been removed. Live testing on Unraid 7.3.1 / kernel 6.18.33 showed `mdunraidd` workers for every source, so it did not identify original applications for this workload.

Updating the container stops the old capture through its normal shutdown and loads a version with no tracing worker. Existing `ATTRIBUTION_ENABLED=true` settings no longer start tracing. Idle profiling and timer modeling continue. Old source rows are preserved in CSV/JSON exports only and no longer populate the dashboard. There is no replacement capture running yet.

## Replacement investigation

The path to test is **FUSE request dispatch → shfs backing-file access → array disk**, with the originating request PID mapped to its container. FUSE carries the original PID in its request header; the physical disk worker does not preserve this relationship for our purposes.

This needs verification against the shfs/libfuse build installed on this host. A request dispatch probe may let us associate a worker thread with a specific FUSE request for the duration of its callback. Observing the backing file operation within that context could then identify the disk. A process seen at approximately the same time is not sufficient evidence.

### Next compatibility check

Run the contents of [request-trace-preflight.sh](../scripts/request-trace-preflight.sh) in the Unraid terminal and return the output. It reads kernel interface lists and ELF symbol metadata for shfs and its loaded FUSE library. It does not enable tracing, attach probes, scan shares, or install tools.

The check establishes whether usable request-dispatch symbols, syscall tracepoints, dynamic probes and kernel BTF are available. The old block-event compatibility output did not establish these capabilities.

### Acceptance gate before a replacement backend ships

1. Observe one known container's operation through `/mnt/user` and recover the original request PID/request identifier at dispatch.
2. Connect it to an actual backing disk access within that request, not merely by timestamp proximity. Verify container identity separately.
3. Repeat with two containers operating concurrently on different disks; neither may be attributed to the other's requests.
4. Verify request completion, worker reuse, short-lived processes, restart and lost-event handling do not retain a stale source.
5. Test cached access, buffered writes, metadata operations and parity work. Leave unsupported causal connections unknown and do not claim a physical spin-up from file activity alone.

Until that proof succeeds, the dashboard says replacement capture is pending. No guessed container names or replacement production tracer will be presented as working.

## Existing extra mounts and permissions

The retired tracer no longer uses `/host/tracing`, `/host/processes` or `/host/docker-containers`. They can be removed from the container; ordinary profiling only needs the original appdata, diskstats and sysfs mounts. The replacement's requirements will be chosen after the compatibility test.

Root mode may have created root-owned SQLite sidecar files. If reverting to the standard `--user=99:100 --cap-drop=ALL --security-opt=no-new-privileges:true`, stop the container and restore ownership of this app's appdata directory to 99:100 from Unraid first. Do not clear the database. Keep Privileged off.

## Sources and limits

- [Linux 6.18 FUSE request creation](https://raw.githubusercontent.com/torvalds/linux/v6.18/fs/fuse/dev.c): the request header is populated with the originating PID in the FUSE connection's PID namespace.
- [libfuse request handling](https://raw.githubusercontent.com/libfuse/libfuse/master/lib/fuse_lowlevel.c): request processing retains a unique identifier and request context. This establishes a potential approach, not the ABI of Unraid's installed shfs build.
- [Linux ftrace](https://docs.kernel.org/trace/ftrace.html): dynamic probes and separate trace instances provide mechanisms to investigate the dispatch path.
- [Disk Talkers runtime model](https://raw.githubusercontent.com/silkyclouds/unraid-disk-talkers/main/README.md): combines recent file activity, open processes and Docker metadata. Those are useful candidate signals, but do not by themselves prove a per-request FUSE-to-disk connection.
