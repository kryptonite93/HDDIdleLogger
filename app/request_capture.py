"""Own the optional worker process and persist only resumed-I/O evidence."""
import json
import os
import subprocess
import sys
import threading
import time


class RequestCapture:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.stopping = threading.Event()
        self.process_lock = threading.Lock()
        self.thread = self.process = None
        self.info = {'enabled': True, 'state': 'starting', 'error': None,
                     'idle_threshold_seconds': 60, 'dropped_events': 0,
                     'last_check_at': None, 'traced_disks': 0}

    def status(self):
        result = dict(self.info)
        if result['state'] == 'capturing' and result['last_check_at'] and time.time()-result['last_check_at'] > 5:
            result.update(state='waiting', error='Request capture is not reporting new readings.')
        return result

    def persist(self, event):
        candidates = event['candidates']
        owner = candidates[0] if event['attribution'] == 'request_likely' else {}
        with self.db.connect() as connection:
            disk = connection.execute('SELECT present,enabled,eligible FROM disks WHERE device_name=?', (event['disk_name'],)).fetchone()
            if not disk or not all(disk):
                return
            connection.execute('''INSERT INTO attribution_events
                (disk_name,observed_at,quiet_seconds,process,pid,container_id,container_name,
                 attribution,operation,bytes,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (event['disk_name'], event['observed_at'], event['quiet_seconds'],
                 owner.get('process', 'Multiple sources' if candidates else 'Unknown source'),
                 owner.get('pid', 0), owner.get('container_id'), owner.get('container_name'),
                 event['attribution'], event['operation'], event['bytes'], json.dumps(event)))
            # Bounded retention for the new capture; legacy export history stays intact.
            connection.execute("DELETE FROM attribution_events WHERE attribution LIKE 'request_%' AND observed_at < ?", (time.time()-30*86400,))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        self.thread = threading.Thread(target=self.run, name='request-capture', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopping.is_set():
            cleanup_failed = False
            try:
                self.info.update(state='starting', error=None, last_check_at=None)
                env = dict(os.environ, DISKSTATS_PATH=str(self.config.diskstats_path), PYTHONUNBUFFERED='1')
                with self.process_lock:
                    if self.stopping.is_set():
                        break
                    process = subprocess.Popen([sys.executable, '-m', 'app.request_worker'],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
                    self.process = process
                if self.stopping.is_set():
                    process.terminate()
                for line in process.stdout:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if value.get('kind') == 'resumed_io':
                        self.persist(value)
                    elif value.get('state') == 'capturing':
                        self.info.update(value, error=None, last_check_at=time.time())
                    elif value.get('state') == 'heartbeat':
                        self.info['last_check_at'] = value['last_check_at']
                    elif value.get('state') in ('error', 'proof_error', 'cleanup_needs_attention'):
                        cleanup_failed |= value['state'] == 'cleanup_needs_attention'
                        self.info.update(state='error', error=value.get('error') or 'Probe cleanup needs attention; inspect container logs.')
                        print('Request capture: '+json.dumps(value), flush=True)
                process.wait()
            except Exception as exc:
                self.info.update(state='error', error=str(exc))
            finally:
                process = self.process
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        cleanup_failed = True
                        self.info.update(state='error', error='Capture did not stop cleanly. Inspect hddproof trace groups before restarting.')
                self.process = None
            if cleanup_failed:
                break  # Never accumulate probes by retrying failed cleanup.
            if self.info['state'] == 'capturing':
                self.info.update(state='starting', last_check_at=None)
            self.stopping.wait(10)

    def stop(self):
        self.stopping.set()
        with self.process_lock:
            process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                self.info.update(state='error', error='Forced capture shutdown; check trace group cleanup in container logs.')
        if self.thread:
            self.thread.join(timeout=5)
