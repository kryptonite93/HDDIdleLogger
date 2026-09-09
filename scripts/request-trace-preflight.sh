#!/bin/sh
# Read-only kernel interfaces and shfs/libfuse ELF metadata. No probes or share scans.
set -u
printf 'Request-level tracing compatibility\n'
uname -r -m
printf '\nBTF type information\n'
if [ -r /sys/kernel/btf/vmlinux ]; then
    printf 'vmlinux BTF: available\n'
else
    printf 'vmlinux BTF: unavailable\n'
fi
for trace_root in /sys/kernel/tracing /sys/kernel/debug/tracing; do
    if [ -d "$trace_root/events" ]; then
        printf '\nTrace root: %s\n' "$trace_root"
        for control in kprobe_events uprobe_events dynamic_events; do
            if [ -w "$trace_root/$control" ]; then
                printf '%s: writable (not modified)\n' "$control"
            else
                printf '%s: unavailable or not writable\n' "$control"
            fi
        done
        printf '\nFUSE event formats\n'
        for format in "$trace_root"/events/fuse/*/format; do
            if [ -r "$format" ]; then cat "$format"; fi
        done
        printf '\nRelevant kernel probe targets\n'
        if [ -r "$trace_root/available_filter_functions" ]; then
            grep -E '^(fuse_(dev_do_read|dev_read|simple_request|request_send|request_end)|do_sys_openat2|vfs_(read|write|iter_read|iter_write)|security_file_open)([.[:space:]]|$)' "$trace_root/available_filter_functions" || true
        fi
        printf '\nFile I/O syscall events\n'
        for event in sys_enter_read sys_enter_pread64 sys_enter_readv sys_enter_write sys_enter_pwrite64 sys_enter_openat sys_enter_openat2 sys_exit_openat sys_exit_openat2; do
            if [ -r "$trace_root/events/syscalls/$event/format" ]; then
                printf '%s: present\n' "$event"
            fi
        done
        break
    fi
done
printf '\nSystem ELF inspection tools\n'
for tool in readelf nm objdump; do
    if command -v "$tool" >/dev/null 2>&1; then printf '%s: available\n' "$tool";
    else printf '%s: unavailable\n' "$tool"; fi
done
inspect_elf() {
    case "$1" in
        /usr/lib/*|/usr/lib64/*|/usr/libexec/*|/usr/local/sbin/*|/lib/*|/lib64/*) ;;
        *) printf 'Non-system executable path skipped\n'; return ;;
    esac
    printf '\nSystem ELF: %s\n' "$1"
    if command -v readelf >/dev/null 2>&1; then
        readelf -n "$1" 2>/dev/null | grep 'Build ID' || true
        readelf -Ws "$1" 2>/dev/null | grep -E 'fuse_(session_(process|receive)|get_context|req_ctx|reply_)' | awk 'NR<=80' || true
    else
        printf 'readelf unavailable; no symbols inspected\n'
    fi
}
printf '\nshfs and loaded FUSE libraries\n'
for proc_dir in /proc/[0-9]*; do
    if [ -r "$proc_dir/comm" ] && [ "$(cat "$proc_dir/comm" 2>/dev/null)" = shfs ]; then
        printf 'shfs PID: %s\n' "${proc_dir##*/}"
        exe_path=$(readlink "$proc_dir/exe")
        inspect_elf "$exe_path"
        if [ -r "$proc_dir/maps" ]; then
            awk '$6 ~ /\/libfuse[^/]*\.so/ {print $6}' "$proc_dir/maps" | sort -u | while IFS= read -r library; do
                inspect_elf "$library"
            done
        fi
    fi
done
printf '\nUser-share and array mount mapping\n'
awk '$5 ~ /^\/mnt\/(user0?|disk[0-9]+)$/ {print $3, $4, $5}' /proc/self/mountinfo
printf '\nDone. No probes were attached and no tracing settings changed.\n'
