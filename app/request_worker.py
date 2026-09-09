"""Optional tracing child process; termination runs tracefs cleanup."""
import importlib.util
import json
import os
from pathlib import Path
import time

from .diskstats import parse_diskstats
from .request_sources import ActivityMatcher, container_name, disk_mapping


def main():
    proc = Path(os.getenv('REQUEST_PROC_PATH', '/host/processes'))
    root = Path(os.getenv('REQUEST_TRACEFS_PATH', '/host/tracing'))
    library = Path(os.getenv('REQUEST_LIBRARY_PATH', '/host/libfuse.so'))
    ini = Path(os.getenv('REQUEST_DISKS_PATH', '/host/unraid/disks.ini'))
    stats = Path(os.getenv('DISKSTATS_PATH', '/host/proc/diskstats'))
    docker = Path(os.getenv('REQUEST_DOCKER_PATH', '/host/docker-containers'))
    quiet = max(60, min(3600, int(os.getenv('REQUEST_QUIET_SECONDS', '60'))))
    if not library.is_file() or library.stat().st_size > 32*1024*1024:
        raise ValueError('Host libfuse library missing or unexpectedly large')
    if not re_path(library):
        raise ValueError('Library mount path contains unsupported characters')
    mapping = disk_mapping(ini.read_text())
    def output(value):
        print(json.dumps(value), flush=True)
    def save(value):
        for candidate in value['candidates']:
            candidate['container_name'] = container_name(docker, candidate['container_id'])
        output({'kind': 'resumed_io', **value})
    matcher = ActivityMatcher(mapping, save, quiet)
    spec = importlib.util.spec_from_file_location('trace_proof', Path(__file__).parents[1]/'scripts/fuse-request-proof.py')
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
    workers = proof.shfs_threads(proc)
    last_tick = last_inventory = 0
    started = False
    def emit(value):
        nonlocal started
        if value.get('kind') == 'candidate_request_to_open':
            matcher.request(value)
        elif value.get('state') == 'correlation_reset':
            matcher.reset()
        elif value.get('state') == 'proof_running':
            started = True
            output({'state': 'capturing', 'traced_disks': len(mapping), 'idle_threshold_seconds': quiet})
        elif value.get('state') in ('proof_error', 'cleanup_needs_attention', 'cleanup_complete'):
            output(value)
    def tick():
        nonlocal last_tick, last_inventory
        now = time.monotonic()
        if not started or now-last_tick < 1:
            return
        last_tick = now
        if now-last_inventory >= 10:
            last_inventory = now
            if disk_mapping(ini.read_text()) != mapping or proof.shfs_threads(proc) != workers:
                raise ValueError('Disk mapping or shfs workers changed; restarting capture with a fresh baseline')
        matcher.sample(parse_diskstats(stats.read_text()), now, time.time())
        output({'state': 'heartbeat', 'last_check_at': time.time()})
    proof.capture(library, proof.elf_symbols(library.read_bytes()), root, proc, 3600,
                  output=emit, tick=tick, all_events=True)


def re_path(path):
    import re
    return bool(re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'state': 'error', 'error': str(exc)}), flush=True)
        raise SystemExit(1)
