#!/bin/sh
# Read-only capability check. Does not enable tracing or access block devices.
set -u
printf 'HDD Idle Profiler attribution compatibility\n'
printf 'Kernel: '
uname -r
printf 'Identity: '
id -u
printf '\nTracing interfaces\n'
for root in /sys/kernel/tracing /sys/kernel/debug/tracing; do
    if [ -d "$root/events" ]; then
        printf 'Trace root: %s\n' "$root"
        if [ -d "$root/instances" ]; then
            printf 'Isolated trace instances: present\n'
        fi
        for event in block_rq_issue block_bio_queue block_bio_remap; do
            format="$root/events/block/$event/format"
            if [ -r "$format" ]; then
                printf '\n%s format:\n' "$event"
                cat "$format"
            else
                printf '%s: unavailable or unreadable\n' "$event"
            fi
        done
        break
    fi
done
printf '\nOptional tools\n'
for tool in python3 bpftrace trace-cmd docker; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf '%s: available\n' "$tool"
    else
        printf '%s: unavailable\n' "$tool"
    fi
done
printf '\nCgroup mode\n'
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
    printf 'v2 controllers: '
    cat /sys/fs/cgroup/cgroup.controllers
else
    printf 'No cgroup v2 controllers file\n'
fi
printf '\nKernel tracing configuration (if exposed)\n'
if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz | grep -E '^CONFIG_(FTRACE|TRACING|TRACEPOINTS|BLK_DEV_IO_TRACE|BPF|BPF_SYSCALL|BPF_EVENTS|DEBUG_INFO_BTF)=' || true
fi
printf '\nDone. No tracing settings were changed.\n'
