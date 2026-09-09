"""Status for retired physical-disk attribution; no tracing is started."""


class SourceStatus:
    def status(self):
        return {
            'enabled': False,
            'state': 'retired',
            'error': 'Request capture is off. Enable the optional request-capture configuration to record likely sources when disk I/O resumes. Existing records are kept; the old physical-disk tracer remains removed.',
            'idle_threshold_seconds': 60,
            'dropped_events': 0,
            'last_check_at': None,
            'traced_disks': 0,
        }
