# Replacing physical-disk attribution

**Current implementation:** [Enable optional request capture](REQUEST-CAPTURE.md). The notes below document the diagnostic investigation and validation needed for stronger physical wake-up claims.

## Retirement and replacement

The old `block_bio_queue` tracer has been removed. Live testing on Unraid 7.3.1 / kernel 6.18.33 showed `mdunraidd` workers for every source, so it did not identify original applications for this workload.

Existing `ATTRIBUTION_ENABLED=true` settings no longer start tracing. Idle profiling and timer modeling continue. Old source rows are preserved in CSV/JSON exports only and no longer populate the dashboard. The replacement is separately opt-in through `REQUEST_ATTRIBUTION_ENABLED=true` and the mounts in the setup guide. It reports likely sources from request-scoped opens near resumed physical I/O, with ambiguity and unknown results shown explicitly.

## Replacement investigation

The path to test is **FUSE request dispatch → shfs backing-file access → array disk**, with the originating request PID mapped to its container. FUSE carries the original PID in its request header; the physical disk worker does not preserve this relationship for our purposes.

This needs verification against the shfs/libfuse build installed on this host. A request dispatch probe may let us associate a worker thread with a specific FUSE request for the duration of its callback. Observing the backing file operation within that context could then identify the disk. A process seen at approximately the same time is not sufficient evidence.

### Compatibility result and bounded proof

The supplied Unraid 6.18.33 x86-64 preflight found writable dynamic probes, FUSE request events, `do_sys_openat2`, and shfs using `/usr/lib64/libfuse3.so.3.16.2`. BTF, ELF inspection tools and file syscall tracepoints were unavailable. The original read-only check remains in [request-trace-preflight.sh](../scripts/request-trace-preflight.sh).

The standalone [fuse-request-proof.py](../scripts/fuse-request-proof.py) inspects ELF symbols itself using Python from the already installed profiler image. It reads the public x86-64 `fuse_ctx` PID field when `fuse_req_ctx` returns, scopes it to an exported `fuse_fs_*` callback on the same shfs worker, and observes successful `do_sys_openat2` calls for absolute `/mnt/diskN/...` paths inside that callback. libfuse 3.16.2's `req_fuse_prepare` reads this request context before dispatching the high-level file callback. Runtime capture now works with the inspected binary; causal coverage remains under validation.

Run this in the Unraid root terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/kryptonite93/HDDIdleLogger/main/scripts/run-request-proof.sh -o /tmp/run-request-proof.sh && sh /tmp/run-request-proof.sh
```

After `proof_running` appears, use a known container to open an existing file on an array-backed `/mnt/user` share. Allow the test to finish, then return the output and which container you used. The launcher prints container IDs/names for comparison. A zero-match run is useful diagnostic evidence; it does not mean no application accessed a disk.

The launcher pins its Python payload to a tested commit instead of downloading it from mutable `main`. When repeating a test immediately after an update, download the launcher using the exact commit URL supplied with that update as well. Confirm `diagnostic_version` is 4 before performing the file operation; a version 3 result does not include the source-summary fix. This avoids repeating the host observation where an updated command still retrieved the older diagnostic.

If setup fails before `proof_running`, no capture has started. The diagnostic prints `proof_error` with the exact failed operation, tracefs path, requested setting or probe definition, error number and any matching kernel diagnostics before cleanup. Return that line together with the cleanup result. Shared kernel error logs are read without clearing them; unrelated probe errors are excluded.

Diagnostic version 3 fixes a setup failure found at the first `uprobe_events` command: Python's high-level append mode performs `SEEK_END`, but the kernel's `seq_lseek` handler rejects that operation with `EINVAL`, before parsing the probe command. Probe registration and removal now use direct append writes without seeking, truncating or creating control files. A Linux regression test reproduces the old failure on a writable `seq_file` and checks the corrected writer without attaching probes. Subsequent host captures successfully reached `proof_running` and completed cleanup.

This **enables temporary probes for 60 seconds** in a separate container, with root and `DAC_OVERRIDE`, a writable tracing mount, and read-only mounts of host process metadata, libfuse and the script. It requires no host Python installation, BTF, Privileged mode, Docker socket, array mounts or appdata access. The existing profiler continues running. The container has no network access; the launcher downloads only the diagnostic script before starting it.

The test uses a unique `hddproof_...` trace instance and probe group. Normal exit, startup failure, Ctrl+C and termination attempt to remove only its probes. Confirm the final output contains `cleanup_complete`; return any `cleanup_needs_attention` output. Avoid forcibly killing the test. Closing its free-buffer handle disables recording, but a forced kill can leave probe definitions requiring cleanup of that specific group.

Output contains process/thread IDs, process names, container IDs and disk numbers. Version 4 reserves 20 live examples for container requests and five for host requests, then prints `candidate_source_summary` rows with totals by container/process/disk, containers first. Summaries retain up to 256 distinct combinations for each category; any excess is counted explicitly as omitted opens. Repeated host activity cannot consume the container output allowance. File paths are read temporarily by the trace instance to identify the backing mount but are not printed or written into the profiler database. Trace-buffer loss aborts the run and invalidates all candidates from that run.

The first successful host capture recorded 1,198 matched backing opens, including 22 with Docker container IDs, and completed cleanup without a reported trace-loss error. The version 3 first-20-example limit showed only `emhttpd` activity and hid those container identities. This establishes that the probes can run on this host, but does not yet confirm Plex's backing disk or satisfy the full acceptance gate.

The version 4 capture during user-reported Plex episode playback identified `Plex Transcoder` in the Docker container named `plex`, with one matched backing open on **disk10**. The captured container ID matched the Docker listing included in that run. It separately recorded one Deluge open on disk4, 20 Unpackerr opens across eight disks, and 134 Krusader opens across all 21 array disks. Host `emhttpd` accounted for 1,218 opens. These are counts of successful opens, not read counts or spin-up counts. All 1,374 matched opens were represented in the summaries, 156 were container-linked, and the run finished with `cleanup_complete` and no reported trace-loss or callback-mismatch error.

The user independently confirmed that the played episode resides on disk10, validating this Plex-to-backing-disk observation. This is evidence for identifying original container requesters through shfs rather than reporting an array worker. The other containers were concurrent background activity, not a controlled two-container test with known file locations. Directory opens and cached file opens can appear in these diagnostic results without any physical disk I/O. The optional integration now filters directory-flagged opens, maps `diskN` through Unraid's `disks.ini`, and requires physical counter activity; its connection from an open to that counter activity remains a time correlation requiring live acceptance.

**Scope:** `candidate_request_to_open` means a candidate connection to a successful backing-file open, not a proven physical disk read or spin-up. Opens may be served from cache. This proof does not cover already-open file descriptors, relative backing paths, direct `/mnt/diskN` access, deferred writes, parity work or newly created shfs workers. Container identity can be unavailable for short-lived processes. Unsupported operations remain unknown. The integration restarts capture when worker membership changes; capture remains off by default.

### Acceptance checks and stronger causal claims

1. Observe one known container's operation through `/mnt/user` and recover the original request PID/request identifier at dispatch.
2. Connect it to an actual backing disk access within that request, not merely by timestamp proximity. Verify container identity separately.
3. Repeat with two containers operating concurrently on different disks; neither may be attributed to the other's requests.
4. Verify request completion, worker reuse, short-lived processes, restart and lost-event handling do not retain a stale source.
5. Test cached access, buffered writes, metadata operations and parity work. Leave unsupported causal connections unknown and do not claim a physical spin-up from file activity alone.

The request-to-open observation above validates one part of this chain. The optional integration exposes the remaining uncertainty as likely, multiple or unknown sources and does not claim proven spin-ups. Its new counter matching and directory filtering still need the live acceptance procedure in the setup guide; the checks above remain necessary for stronger causal claims.

## Existing extra mounts and permissions

Ordinary profiling only needs the original appdata, diskstats and sysfs mounts. Optional request capture also uses tracing, host process metadata, libfuse, Unraid mapping and optional Docker metadata mounts, as listed in the setup guide. Those extra mounts can be removed when request capture is disabled.

Root mode may have created root-owned SQLite sidecar files. If reverting to the standard `--user=99:100 --cap-drop=ALL --security-opt=no-new-privileges:true`, stop the container and restore ownership of this app's appdata directory to 99:100 from Unraid first. Do not clear the database. Keep Privileged off.

## Sources and limits

- [Linux 6.18 FUSE request creation](https://raw.githubusercontent.com/torvalds/linux/v6.18/fs/fuse/dev.c): the request header is populated with the originating PID in the FUSE connection's PID namespace.
- [libfuse 3.16.2 high-level dispatch](https://raw.githubusercontent.com/libfuse/libfuse/fuse-3.16.2/lib/fuse.c): `req_fuse_prepare` copies the request context before calling file-operation callbacks.
- [libfuse 3.16.2 public request context](https://raw.githubusercontent.com/libfuse/libfuse/fuse-3.16.2/include/fuse_lowlevel.h): the public `fuse_ctx` layout includes the requesting thread ID.
- [Linux user-space probes](https://docs.kernel.org/trace/uprobetracer.html): entry/return probes use file offsets and support reading return-value memory.
- [Linux ftrace](https://docs.kernel.org/trace/ftrace.html): dynamic probes and separate trace instances provide mechanisms to investigate the dispatch path.
- [Disk Talkers runtime model](https://raw.githubusercontent.com/silkyclouds/unraid-disk-talkers/main/README.md): combines recent file activity, open processes and Docker metadata. Those are useful candidate signals, but do not by themselves prove a per-request FUSE-to-disk connection.
