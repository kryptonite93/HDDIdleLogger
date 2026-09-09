#!/bin/sh
# Temporary request-to-backing-open diagnostic for the inspected Unraid host.
set -eu

if [ "$(id -u)" != 0 ]; then
    echo 'Run this diagnostic in the Unraid root terminal.' >&2
    exit 1
fi
library=/usr/lib64/libfuse3.so.3.16.2
trace_root=/sys/kernel/debug/tracing
if [ ! -r "$library" ] || [ ! -w "$trace_root/uprobe_events" ]; then
    echo 'The inspected libfuse library or writable tracing interface is unavailable.' >&2
    exit 1
fi
image=ghcr.io/kryptonite93/hddidlelogger:latest
if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo 'The profiler image must already be installed on this host.' >&2
    exit 1
fi
work=$(mktemp -d /tmp/hdd-request-proof.XXXXXX)
cleanup() {
    rm -f "$work/proof.py"
    rmdir "$work"
}
trap cleanup EXIT
curl -fsSL https://raw.githubusercontent.com/kryptonite93/HDDIdleLogger/main/scripts/fuse-request-proof.py -o "$work/proof.py"
echo 'Starting a 60-second test. When proof_running appears, use a known container to open an existing file on an array-backed user share.'
result=0
docker run --rm --name hdd-request-proof --pull=never --network=none \
    --user=0:0 --cap-drop=ALL --cap-add=DAC_OVERRIDE \
    --security-opt=no-new-privileges:true \
    --mount "type=bind,src=$library,dst=/host/libfuse.so,readonly" \
    --mount "type=bind,src=$trace_root,dst=/host/tracing" \
    --mount type=bind,src=/proc,dst=/host/processes,readonly \
    --mount "type=bind,src=$work/proof.py,dst=/proof.py,readonly" \
    --entrypoint python "$image" /proof.py --capture-seconds 60 || result=$?
if [ "$result" -ne 0 ]; then
    echo 'The test failed. Return the proof_error, cleanup and proof_failed lines above; no container operation is needed until proof_running appears.'
    exit "$result"
fi
echo 'Container IDs and names for checking the result:'
docker ps --no-trunc --format '{{.ID}} {{.Names}}'
exit "$result"
