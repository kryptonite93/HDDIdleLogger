"""Explicit, bounded libfuse 3/x86-64 proof. No profiler database writes.

Inspect is read-only. Capture installs unique tracefs probes and removes them on exit.
Uses the public fuse_ctx ABI (uid, gid, pid, umask), not kernel structure offsets.
"""
import argparse
from collections import Counter
import errno
import json
import os
from pathlib import Path
import re
import signal
import struct
import time
import uuid


def elf_symbols(data):
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('Expected a 64-bit little-endian ELF library')
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    if header[2] != 62 or header[9] != 56 or header[11] != 64:
        raise ValueError('Only standard x86-64 ELF headers are supported')

    def bounded(offset, size):
        if offset < 0 or size < 0 or offset+size > len(data):
            raise ValueError('ELF section outside file')
        return data[offset:offset+size]

    segments = []
    for index in range(header[10]):
        segment = struct.unpack('<IIQQQQQQ', bounded(header[5]+index*56, 56))
        if segment[0] == 1 and segment[1] & 1:
            segments.append(segment)
    sections = [struct.unpack('<IIQQQQIIQQ', bounded(header[6]+index*64, 64)) for index in range(header[12])]
    result = {}
    for section in sections:
        if section[1] not in (2, 11):
            continue
        if section[9] != 24 or section[6] >= len(sections):
            raise ValueError('Unsupported ELF symbol table')
        strings = sections[section[6]]
        names = bounded(strings[4], strings[5])
        table = bounded(section[4], section[5])
        for index in range(0, len(table)-23, 24):
            name, info, _, target_section, value, _ = struct.unpack_from('<IBBHQQ', table, index)
            if not target_section or info & 15 != 2 or name >= len(names):
                continue
            label = names[name:].split(b'\0', 1)[0].decode('ascii', errors='replace')
            if not label.startswith(('fuse_req_ctx', 'fuse_fs_', 'fuse_version')):
                continue
            for segment in segments:
                if segment[3] <= value < segment[3]+segment[5]:
                    result[label] = segment[2]+value-segment[3]
                    break
    return result


LINE = re.compile(r'^.*-(\d+)\s+\[\d+\].*?\s(\d+\.\d+):\s+(\w+):\s+(.*)$')
FIELD = re.compile(r'(\w+)=(?:"([^"\n]*)"|([^\s]+))')
CID = re.compile(r'(?:^|/)(?:docker-)?([a-f0-9]{64})(?:\.scope)?(?:/|$)')
DISK = re.compile(r'^/mnt/(disk[1-9][0-9]*)(?:/|$)')


def parse_line(line):
    match = LINE.match(line)
    if not match:
        return None
    tid, stamp, event, payload = match.groups()
    return int(tid), float(stamp), event, {m[0]: m[1] or m[2] for m in FIELD.findall(payload)}


def origin(proc, pid, stamp):
    try:
        root = proc/str(pid)
        before = (root/'stat').read_text().rsplit(')', 1)[1].split()[19]
        boot_offset = time.clock_gettime(time.CLOCK_BOOTTIME)-time.monotonic() if hasattr(time, 'CLOCK_BOOTTIME') else 0
        if int(before)/os.sysconf('SC_CLK_TCK') > stamp+boot_offset:
            return None
        command = (root/'comm').read_text().strip()
        cgroup = (root/'cgroup').read_text()
        if before != (root/'stat').read_text().rsplit(')', 1)[1].split()[19]:
            return None
        ids = {m.group(1) for line in cgroup.splitlines() for m in CID.finditer(line.split(':', 2)[-1])}
        return {'pid': pid, 'process': command, 'container_id': next(iter(ids)) if len(ids) == 1 else None}
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class Correlator:
    def __init__(self, resolve, emit, all_events=False):
        self.resolve, self.emit = resolve, emit
        self.all_events = all_events
        self.pending = {}
        self.prepared = {}
        self.scopes = {}
        self.opens = {}
        self.counts = Counter()
        self.sources = {'container': Counter(), 'host': Counter()}
        self.examples = Counter()

    def source_summaries(self):
        rows = []
        for category in ('container', 'host'):
            for (cid, process, disk), count in sorted(self.sources[category].items(), key=lambda item: (-item[1], item[0])):
                rows.append({'kind': 'candidate_source_summary', 'container_id': cid,
                             'process': process, 'disk': disk, 'matched_backing_opens': count,
                             'physical_spin_up_proven': False})
        return rows

    def reset(self):
        self.pending.clear()
        self.prepared.clear()
        self.scopes.clear()
        self.opens.clear()
        if self.all_events:
            self.emit({'state': 'correlation_reset'})

    def accept(self, record):
        if not record:
            return
        tid, stamp, event, fields = record
        self.counts[event] += 1
        try:
            if event == 'ctx_in':
                # A new request context invalidates any preceding prepared identity.
                self.pending[tid] = (int(fields['request']), stamp)
                self.prepared.pop(tid, None)
                self.scopes.pop(tid, None)
                self.opens.pop(tid, None)
            elif event == 'ctx_out':
                entry = self.pending.pop(tid, None)
                if entry and 0 <= stamp-entry[1] <= 1 and int(fields['context']) and int(fields['origin']) > 0:
                    identity = self.resolve(int(fields['origin']), stamp)
                    if identity:
                        self.counts['resolved_contexts'] += 1
                        self.prepared[tid] = (entry[0], stamp, identity)
            elif event.startswith('cb_in_'):
                scope = self.scopes.get(tid)
                if scope:
                    scope['callbacks'].append(event[6:])
                else:
                    prepared = self.prepared.pop(tid, None)
                    if prepared and 0 <= stamp-prepared[1] <= 1:
                        self.scopes[tid] = dict(token=prepared[0], start=stamp, identity=prepared[2], callbacks=[event[6:]])
            elif event.startswith('cb_out_'):
                scope = self.scopes.get(tid)
                if scope:
                    if scope['callbacks'][-1] != event[7:]:
                        self.counts['callback_mismatches'] += 1
                        self.reset()
                        return
                    scope['callbacks'].pop()
                    if not scope['callbacks']:
                        self.scopes.pop(tid, None)
                        self.opens.pop(tid, None)
            elif event == 'backing_open':
                scope = self.scopes.get(tid)
                disk = DISK.match(fields.get('filename', ''))
                self.opens.pop(tid, None)
                if scope:
                    self.counts['opens_inside_callback'] += 1
                    if not disk:
                        self.counts['unmapped_path_opens'] += 1
                if scope and disk and 0 <= stamp-scope['start'] <= 30:
                    flags = int(fields.get('flags', '-1'))
                    if self.all_events and (flags < 0 or flags & (0x10000 | 0x200000) or fields['filename'].rstrip('/') == '/mnt/'+disk.group(1)):
                        self.counts['excluded_directory_or_unknown_flags'] += 1
                        return
                    self.opens[tid] = (scope, disk.group(1), stamp)
            elif event == 'backing_done':
                opened = self.opens.pop(tid, None)
                if opened and int(fields['fd']) >= 0 and self.scopes.get(tid) is opened[0] and 0 <= stamp-opened[2] <= 30:
                    identity = opened[0]['identity']
                    self.counts['matched_backing_opens'] += 1
                    if identity['container_id']:
                        self.counts['container_backing_opens'] += 1
                    category = 'container' if identity['container_id'] else 'host'
                    key = (identity['container_id'], identity['process'], opened[1])
                    sources = self.sources[category]
                    # Separate bounds keep busy host activity from consuming
                    # either the container examples or the container summary.
                    if key in sources or len(sources) < 256:
                        sources[key] += 1
                    else:
                        self.counts[category+'_summary_omitted_opens'] += 1
                    if self.all_events or self.examples[category] < (20 if category == 'container' else 5):
                        self.examples[category] += 1
                        self.emit({'kind': 'candidate_request_to_open', 'disk': opened[1],
                                   'worker_tid': tid, 'monotonic': stamp, **identity,
                                   'physical_spin_up_proven': False})
        except (KeyError, ValueError):
            self.counts['invalid_fields'] += 1
            self.reset()


def shfs_threads(proc):
    result = set()
    for process in proc.glob('[0-9]*'):
        try:
            if (process/'comm').read_text().strip() == 'shfs':
                result.update(int(t.name) for t in (process/'task').iterdir() if t.name.isdigit())
        except OSError:
            continue
    return result


def append_probe_command(path, command):
    # Python open(..., 'a') performs SEEK_END during initialization. tracefs
    # probe controls use seq_lseek and reject that seek with EINVAL before any
    # command is written. Raw append writes also avoid O_TRUNC, which would
    # remove other tools' probe definitions, and O_CREAT on missing controls.
    payload = (command+'\n').encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        if os.write(fd, payload) != len(payload):
            raise OSError(errno.EIO, 'Incomplete probe command write', str(path))
    finally:
        os.close(fd)


def failure_diagnostics(root, instance, group, step):
    """Read only this run's kernel diagnostics; never clear shared error logs."""
    result = {}
    for label, path in (('instance_errors', instance/'error_log'), ('probe_errors', root/'error_log')):
        try:
            with path.open() as handle:
                content = handle.read(65536)
            blocks = re.split(r'(?=^\[)', content, flags=re.MULTILINE)
            relevant = [block[:2048] for block in blocks if group in block]
            if relevant:
                result[label] = relevant[-8:]
        except OSError:
            pass
    path = Path(step.get('path', ''))
    if path.name in ('filter', 'trace_clock') and instance in path.parents:
        try:
            with path.open() as handle:
                result['setting_feedback'] = handle.read(4096)
        except OSError:
            pass
    return result


def capture(library, symbols, root, proc, seconds, output=None, tick=None, all_events=False):
    workers = shfs_threads(proc)
    if not workers:
        raise ValueError('No shfs worker threads visible through the host process mount')
    callbacks = sorted(name for name in symbols if name in (
        'fuse_fs_open', 'fuse_fs_create', 'fuse_fs_read', 'fuse_fs_read_buf',
        'fuse_fs_write', 'fuse_fs_write_buf', 'fuse_fs_getattr', 'fuse_fs_opendir', 'fuse_fs_readdir'))
    if 'fuse_req_ctx' not in symbols or not callbacks:
        raise ValueError('Required exported libfuse request/context callback symbols unavailable; no probes attached')
    group = 'hddproof_'+uuid.uuid4().hex[:16]
    instance = root/'instances'/group
    registered = []
    owned = False
    fd = free_fd = None
    stopping = False
    output = output or (lambda value: print(json.dumps(value), flush=True))
    correlation = Correlator(lambda pid, stamp: origin(proc, pid, stamp), output, all_events)
    step = {}

    def mark(operation, path, requested=None):
        step.clear()
        step.update(operation=operation, path=path.as_posix())
        if requested is not None:
            value = str(requested)
            step.update(requested=value[:4096], requested_bytes=len(value.encode()))

    def stop(*_):
        nonlocal stopping
        stopping = True

    old_handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    def write(path, value):
        mark('write_setting', path, value)
        path.write_text(str(value)+'\n')
    def register(control, name, definition):
        mark('register_probe', root/control, definition)
        append_probe_command(root/control, definition)
        registered.append((control, name))
        write(instance/'events'/group/name/'filter', ' || '.join(f'common_pid == {tid}' for tid in sorted(workers)))
        write(instance/'events'/group/name/'enable', 1)

    try:
        output({'state': 'proof_setup', 'diagnostic_version': 4, 'worker_threads': len(workers)})
        mark('create_instance', instance)
        instance.mkdir()
        owned = True
        write(instance/'tracing_on', 0)
        write(instance/'current_tracer', 'nop')
        write(instance/'trace_clock', 'mono')
        write(instance/'buffer_size_kb', 128)
        for option, value in (('context-info', 1), ('latency-format', 0), ('disable_on_free', 1)):
            write(instance/'options'/option, value)
        mark('open_free_buffer', instance/'free_buffer')
        free_fd = os.open(instance/'free_buffer', os.O_RDONLY)
        register('uprobe_events', 'ctx_in', f'p:{group}/ctx_in {library}:{symbols["fuse_req_ctx"]:#x} request=%di:u64')
        register('uprobe_events', 'ctx_out', f'r:{group}/ctx_out {library}:{symbols["fuse_req_ctx"]:#x} context=$retval:u64 origin=+8($retval):u32')
        for index, callback in enumerate(callbacks):
            register('uprobe_events', f'cb_in_{index}', f'p:{group}/cb_in_{index} {library}:{symbols[callback]:#x}')
            register('uprobe_events', f'cb_out_{index}', f'r:{group}/cb_out_{index} {library}:{symbols[callback]:#x}')
        flags = ' flags=+0($arg3):u64' if all_events else ''
        register('kprobe_events', 'backing_open', f'p:{group}/backing_open do_sys_openat2 filename=+u0($arg2):string{flags}')
        register('kprobe_events', 'backing_done', f'r:{group}/backing_done do_sys_openat2 fd=$retval:s64')
        mark('open_trace_pipe', instance/'trace_pipe')
        fd = os.open(instance/'trace_pipe', os.O_RDONLY | os.O_NONBLOCK)
        write(instance/'tracing_on', 1)
        output({'state': 'proof_running', 'seconds': seconds, 'worker_threads': len(workers),
                'callbacks': callbacks, 'notice': 'Perform an ordinary file-open operation from a known container now. No filenames are printed.'})
        mark('capture', instance/'trace_pipe')
        deadline = time.monotonic()+seconds
        pending = ''
        next_check = 0
        losses = 0
        while not stopping and time.monotonic() < deadline:
            if tick:
                tick()
            if time.monotonic() >= next_check:
                lost = 0
                for stats in (instance/'per_cpu').glob('cpu*/stats'):
                    for line in stats.read_text().splitlines():
                        key, _, value = line.partition(':')
                        if key.strip() in ('overrun', 'dropped events', 'commit overrun'):
                            lost += int(value)
                if lost > losses:
                    losses = lost
                    correlation.reset()
                    # Abort rather than risk reconnecting a lost scope boundary.
                    raise ValueError('Trace loss detected. No inference is reliable for this run; retry with a quieter workload.')
                next_check = time.monotonic()+.5
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                data = b''
            if not data:
                time.sleep(.02)
                continue
            pending += data.decode('utf-8', errors='replace')
            lines = pending.split('\n')
            pending = lines.pop()
            if len(pending) > 65536:
                raise ValueError('Oversize trace line')
            for line in lines:
                if 'LOST' in line and 'EVENT' in line:
                    raise ValueError('Trace loss marker; proof aborted')
                correlation.accept(parse_line(line))
        for summary in correlation.source_summaries():
            output(summary)
        output({'state': 'proof_finished', 'counts': dict(correlation.counts),
                'note': 'Candidates require comparison with your known operation. No dashboard attribution or physical spin-up claim was recorded.'})
    except OSError as exc:
        output({'state': 'proof_error', **step, 'errno': exc.errno, 'error': str(exc),
                'registered_probes': len(registered),
                'kernel_diagnostics': failure_diagnostics(root, instance, group, step)})
        raise
    finally:
        errors = []
        if owned:
            try:
                write(instance/'tracing_on', 0)
            except OSError as exc:
                errors.append(str(exc))
            for _, name in reversed(registered):
                try:
                    write(instance/'events'/group/name/'enable', 0)
                except OSError as exc:
                    errors.append(str(exc))
        for descriptor in (fd, free_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    errors.append(str(exc))
        if owned:
            try:
                instance.rmdir()
            except OSError as exc:
                errors.append(str(exc))
        for control, name in reversed(registered):
            try:
                append_probe_command(root/control, f'-:{group}/{name}')
            except OSError as exc:
                errors.append(str(exc))
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        output({'state': 'cleanup_complete' if not errors else 'cleanup_needs_attention', 'group': group, 'errors': errors})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, default=Path('/host/libfuse.so'))
    parser.add_argument('--tracefs', type=Path, default=Path('/host/tracing'))
    parser.add_argument('--proc', type=Path, default=Path('/host/processes'))
    parser.add_argument('--capture-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.capture_seconds <= 60 or not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(args.library)):
        parser.error('Capture must be 0–60 seconds and library must have a simple absolute path')
    if args.library.stat().st_size > 32*1024*1024:
        raise ValueError('Library unexpectedly large')
    symbols = elf_symbols(args.library.read_bytes())
    print(json.dumps({'state': 'elf_inspected', 'symbols': {key: hex(value) for key, value in symbols.items()}}), flush=True)
    if args.capture_seconds:
        capture(args.library, symbols, args.tracefs, args.proc, args.capture_seconds)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, struct.error) as exc:
        print(json.dumps({'state': 'proof_failed', 'error': str(exc)}), flush=True)
        raise SystemExit(1)
