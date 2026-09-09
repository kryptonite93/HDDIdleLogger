"""Optional, isolated tracefs capture. Never opens block devices or user files."""
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)
TRACE = re.compile(r'^.*-(\d+)\s+\[\d+\].*?\s(\d+\.\d+):\s+block_bio_queue:\s+'
                   r'(\d+),(\d+)\s+(\S+)\s+\d+\s+\+\s+(\d+)\s+\[(.*)\]\s*$')
CONTAINER = re.compile(r'(?:^|/)(?:docker-)?([a-f0-9]{64})(?:\.scope)?(?:/|$)')


def parse_trace(line):
    match = TRACE.match(line)
    if not match:
        return None
    pid, stamp, major, minor, operation, sectors, command = match.groups()
    return dict(pid=int(pid), monotonic=float(stamp), device=f'{major}:{minor}',
                operation=operation[:10], bytes=int(sectors)*512, process=command[:64])


def process_source(proc_root, docker_root, event):
    """Resolve a live PID only; never guess from a similar process name."""
    source = dict(container_id=None, container_name=None, attribution='unresolved_process')
    process = proc_root / str(event['pid'])
    try:
        before = (process/'stat').read_text()
        start_ticks = int(before.rsplit(')', 1)[1].split()[19])
        boot_offset = time.clock_gettime(time.CLOCK_BOOTTIME)-time.monotonic() if hasattr(time, 'CLOCK_BOOTTIME') else 0
        if start_ticks / os.sysconf('SC_CLK_TCK') > event['monotonic'] + boot_offset:
            return source  # PID has been reused since this queued trace event.
        if (process/'comm').read_text().strip() != event['process']:
            return source
        cgroup = (process/'cgroup').read_text()
        after = (process/'stat').read_text()
        if before.rsplit(')', 1)[1].split()[19] != after.rsplit(')', 1)[1].split()[19]:
            return source
        ids = {m.group(1) for line in cgroup.splitlines()
               for m in CONTAINER.finditer(line.split(':', 2)[-1])}
        if len(ids) != 1:
            source['attribution'] = 'host_or_kernel' if not ids else 'unresolved_process'
            return source
        container_id = ids.pop()
        source.update(container_id=container_id, attribution='container_cgroup')
        # No Docker socket: read only the name from the optional metadata mount.
        path = docker_root/container_id/'config.v2.json'
        if path.stat().st_size <= 2*1024*1024:
            with path.open() as handle:
                metadata = json.load(handle)
            if metadata.get('ID') == container_id and isinstance(metadata.get('Name'), str):
                source['container_name'] = metadata['Name'].lstrip('/')[:128]
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    return source


class Attribution:
    def __init__(self, db, config, collector):
        self.db, self.config, self.collector = db, config, collector
        self.enabled = getattr(config, 'attribution_enabled', False)
        self.threshold = getattr(config, 'attribution_idle_seconds', 60)
        self.state = 'starting' if self.enabled else 'disabled'
        self.error = None
        self.dropped = 0
        self.last_check_at = None
        self.last = {}
        self.devices = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.instance = None
        self.owns_instance = False
        self.fd = None
        self.free_fd = None
        self.wall_offset = time.time()-time.monotonic()
        self.coverage_since = 0

    def status(self):
        return dict(enabled=self.enabled, state=self.state, error=self.error,
                    idle_threshold_seconds=self.threshold, dropped_events=self.dropped,
                    last_check_at=self.last_check_at, traced_disks=len(self.devices))

    def refresh_devices(self):
        devices = {}
        for disk in self.db.rows('SELECT device_name FROM disks WHERE enabled=1 AND eligible=1 AND present=1'):
            name = disk['device_name']
            try:
                device = (self.config.sys_block_path/name/'dev').read_text().strip()
                if re.fullmatch(r'\d+:\d+', device):
                    devices[device] = name
            except OSError:
                continue
        if devices != self.devices:
            self.last.clear()  # Selection changes break trace continuity.
            self.devices = devices
            values = [(int(d.split(':')[0]) << 20) | int(d.split(':')[1]) for d in devices]
            # Kernel dev_t uses a 20-bit minor field, as exposed in event format.
            expression = ' || '.join(f'dev == {value}' for value in values) or 'dev == 0'
            self.write('events/block/block_bio_queue/filter', expression)

    def write(self, relative, value):
        (self.instance/relative).write_text(str(value))

    def setup(self):
        root = self.config.tracefs_path
        if not (root/'instances').is_dir():
            raise ValueError('Tracefs instances unavailable. Check the optional tracing mount.')
        self.instance = root/'instances'/('hddidle-'+uuid.uuid4().hex)
        self.instance.mkdir()  # A fresh instance; never reconfigure another tracer.
        self.owns_instance = True
        self.write('tracing_on', 0)
        self.write('current_tracer', 'nop')
        self.write('trace_clock', 'mono')
        self.write('buffer_size_kb', 64)
        self.write('options/context-info', 1)
        self.write('options/latency-format', 0)
        if (self.instance/'options/print-tgid').exists():
            self.write('options/print-tgid', 0)
        self.write('options/disable_on_free', 1)
        self.free_fd = os.open(self.instance/'free_buffer', os.O_RDONLY)
        event_format = (self.instance/'events/block/block_bio_queue/format').read_text()
        if not all(field in event_format for field in ('field:dev_t dev;', 'field:char comm[', 'field:sector_t sector;')):
            raise ValueError('Unsupported block_bio_queue format; capture was not enabled.')
        self.write('events/block/block_bio_queue/filter', 'dev == 0')
        self.refresh_devices()
        self.fd = os.open(self.instance/'trace_pipe', os.O_RDONLY | os.O_NONBLOCK)
        self.write('events/block/block_bio_queue/enable', 1)
        self.coverage_since = time.monotonic()
        self.write('tracing_on', 1)
        self.state = 'capturing'

    def accept(self, event):
        name = self.devices.get(event['device'])
        if not name or event['monotonic'] < self.coverage_since:
            return
        previous = self.last.get(name)
        if previous is not None and event['monotonic'] < previous:
            return
        self.last[name] = event['monotonic']
        quiet_seconds = event['monotonic']-previous if previous is not None else None
        if quiet_seconds is not None and quiet_seconds < self.threshold:
            return
        source = process_source(self.config.host_proc_path, self.config.docker_metadata_path, event)
        # Synchronize with clear-history and selection changes in the main collector.
        with self.collector.lock, self.db.connect() as connection:
            selected = connection.execute('SELECT 1 FROM disks WHERE device_name=? AND enabled=1 AND eligible=1 AND present=1', (name,)).fetchone()
            if not selected:
                return
            connection.execute('''INSERT INTO attribution_events(disk_name,observed_at,quiet_seconds,
                process,pid,container_id,container_name,attribution,operation,bytes)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', (name, event['monotonic']+self.wall_offset, quiet_seconds,
                event['process'], event['pid'], source['container_id'], source['container_name'],
                source['attribution'], event['operation'], event['bytes']))
            connection.execute('DELETE FROM attribution_events WHERE id <= COALESCE((SELECT id FROM attribution_events ORDER BY id DESC LIMIT 1 OFFSET 5000), -1)')

    def check_loss(self):
        total = 0
        for path in (self.instance/'per_cpu').glob('cpu*/stats'):
            for line in path.read_text().splitlines():
                key, _, value = line.partition(':')
                if key.strip() in ('overrun', 'dropped events', 'commit overrun'):
                    total += int(value.strip())
        if total > self.dropped:
            self.last.clear()
            self.coverage_since = time.monotonic()
        self.dropped = max(self.dropped, total)

    def run(self):
        pending = ''
        try:
            self.setup()
            next_check = 0
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_check:
                    self.refresh_devices()
                    self.check_loss()
                    offset = time.time()-now
                    if abs(offset-self.wall_offset) > 2:
                        self.last.clear()
                        self.coverage_since = now
                    self.wall_offset = offset
                    self.last_check_at = time.time()
                    next_check = now+2
                try:
                    chunk = os.read(self.fd, 65536)
                except BlockingIOError:
                    chunk = b''
                if not chunk:
                    self.stop_event.wait(.1)
                    continue
                pending += chunk.decode('utf-8', errors='replace')
                lines = pending.split('\n')
                pending = lines.pop()
                if len(pending) > 65536:
                    raise ValueError('Trace record exceeds supported size')
                for line in lines:
                    if 'LOST' in line and 'EVENT' in line:
                        self.last.clear()
                        self.coverage_since = time.monotonic()
                        continue
                    event = parse_trace(line)
                    if event:
                        self.accept(event)
                    elif 'block_bio_queue:' in line:
                        raise ValueError('Unrecognized trace output; capture stopped to avoid misleading results.')
        except Exception as exc:
            self.state, self.error = 'unavailable', f'{type(exc).__name__}: {exc}'
            log.warning('Optional attribution unavailable: %s', self.error)
        finally:
            self.cleanup()

    def cleanup(self):
        if self.owns_instance:
            for relative in ('tracing_on', 'events/block/block_bio_queue/enable'):
                try:
                    self.write(relative, 0)
                except OSError:
                    pass
        for fd in (self.fd, self.free_fd):
            if fd is not None:
                os.close(fd)
        self.fd = self.free_fd = None
        if self.owns_instance:
            try:
                self.instance.rmdir()  # Tracefs removes its virtual files itself.
            except OSError:
                log.warning('Trace instance cleanup incomplete: %s', self.instance)
        self.owns_instance = False
        if self.state == 'capturing':
            self.state = 'stopped'

    def start(self):
        if self.enabled:
            self.thread = threading.Thread(target=self.run, name='io-attribution', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
