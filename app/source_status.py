"""Status for retired physical-disk attribution; no tracing is started."""


class SourceStatus:
    def status(self):
        return {
            'enabled': False,
            'state': 'retired',
            'error': 'The physical-disk tracer was removed because it identified Unraid workers instead of original requesters. Request-level capture is awaiting host validation.',
            'idle_threshold_seconds': 60,
            'dropped_events': 0,
            'last_check_at': None,
            'traced_disks': 0,
        }
