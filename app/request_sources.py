"""Correlate request-scoped opens with resumed physical I/O. No standby claims."""
from collections import deque
import configparser
import json
import re


def disk_mapping(text):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(text)
    result = {}
    for section in parser.sections():
        name = section.strip('"')
        values = parser[section]
        device = values.get('device', '').strip('"')
        if re.fullmatch(r'disk[1-9][0-9]*', name) and re.fullmatch(r'sd[a-z]+', device):
            if device in result.values():
                raise ValueError('Ambiguous Unraid disk mapping')
            result[name] = device
    if not result:
        raise ValueError('No assigned array disks in Unraid disks.ini')
    return result


def container_name(root, cid):
    if not cid or not re.fullmatch('[a-f0-9]{64}', cid):
        return None
    try:
        # Do not expose Config/Env or other Docker metadata.
        with (root/cid/'config.v2.json').open() as handle:
            data = json.load(handle)
        if data.get('ID') != cid:
            return None
        return str(data.get('Name', '')).lstrip('/')[:128] or None
    except (OSError, ValueError, TypeError):
        return None


class ActivityMatcher:
    BEFORE = 10.0
    AFTER = 2.0

    def __init__(self, mapping, emit, quiet=60):
        self.mapping, self.emit, self.quiet = mapping, emit, quiet
        self.previous = {}
        self.last_io = {}
        self.observed_activity = set()
        self.requests = deque()
        self.pending = {}
        self.last_sample = None

    def reset(self):
        self.previous.clear()
        self.last_io.clear()
        self.observed_activity.clear()
        self.requests.clear()
        self.pending.clear()
        self.last_sample = None

    def request(self, event):
        if event.get('disk') not in self.mapping:
            return
        if len(self.requests) >= 4096:
            self.reset()
            raise ValueError('Request buffer exceeded; attribution baseline reset')
        self.requests.append(dict(event))

    def sample(self, counters, now, wall):
        # Stalls, suspend and missed readings cannot establish an idle period.
        if self.last_sample is not None and not 0 < now-self.last_sample <= 3:
            self.reset()
        self.last_sample = now
        for array_disk, device in self.mapping.items():
            current = counters.get(device)
            previous = self.previous.get(device)
            if current is None:
                self.previous.pop(device, None)
                self.last_io.pop(device, None)
                self.pending.pop(device, None)
                self.observed_activity.discard(device)
                continue
            self.previous[device] = current
            if previous is None:
                self.last_io[device] = now
                continue
            delta = current.delta(previous)
            if delta is None:
                self.last_io[device] = now
                self.pending.pop(device, None)
                self.observed_activity.discard(device)
                self.requests = deque(r for r in self.requests if r['disk'] != array_disk)
                continue
            if any(delta.values()):
                quiet = now-self.last_io[device]
                self.last_io[device] = now
                if quiet >= self.quiet:
                    self.pending[device] = {'disk_name': device, 'array_disk': array_disk,
                        'monotonic': now, 'observed_at': wall, 'quiet_seconds': quiet,
                        'quiet_is_lower_bound': device not in self.observed_activity,
                        'bytes': 512*(delta['sectors_read']+delta['sectors_written']),
                        'operation': 'I/O resumed'}
                self.observed_activity.add(device)
        for device, resume in list(self.pending.items()):
            if now-resume['monotonic'] < self.AFTER:
                continue
            matches = {}
            for event in self.requests:
                if event['disk'] == resume['array_disk'] and -self.BEFORE <= event['monotonic']-resume['monotonic'] <= self.AFTER:
                    key = ('container', event['container_id']) if event['container_id'] else ('host', event['pid'], event['process'])
                    matches[key] = {k: event[k] for k in ('pid', 'process', 'container_id')}
            candidates = list(matches.values())[:20]
            state = 'request_likely' if len(matches) == 1 else 'request_multiple' if matches else 'request_unknown'
            self.emit({**resume, 'attribution': state, 'candidates': candidates,
                       'candidate_count': len(matches), 'physical_spin_up_proven': False})
            del self.pending[device]
        self.requests = deque(r for r in self.requests if r['monotonic'] >= now-self.BEFORE-self.AFTER-3)
