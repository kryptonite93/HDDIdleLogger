"""Deterministic synthetic preview; never reads host counters or modifies real history."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.database import Database
from app.main import create_app

root = Path(__file__).resolve().parents[1]
data = root/"data"/"synthetic-preview"
data.mkdir(parents=True, exist_ok=True)
config = SimpleNamespace(data_dir=data, diskstats_path=data/"diskstats",
                         sys_block_path=data/"sys", boot_id_path=data/"boot")
config.boot_id_path.write_text("synthetic-boot")
config.diskstats_path.write_text("".join(f"8 {i*16} sd{chr(97+i)} 100 0 1000 0 200 0 2000 0 0 0 0 0 0 0 0 0 0\n" for i in range(24)))
db = Database(data/"profiler.sqlite3")
db.save_settings(Settings(timezone="America/Toronto"))
with db.connect() as connection:
    for table in ("activity_events", "idle_intervals", "observation_sessions", "collector_events", "disks"):
        connection.execute(f"DELETE FROM {table}")
    now = time.time()
    start = now-4*86400
    for index in range(24):
        name = f"sd{chr(97+index)}"
        model = ["ST18000NM000J", "WDC WD140EDFZ", "ST16000NM001G"][index%3]
        connection.execute("INSERT INTO disks VALUES (?,?,?,?,?,1,1,1,?,?)", (name,model,f"SYNTHETIC-{index+1:03}","Demo",1,start,now))
        gap = [900, 2400, 10800, 21600, 43200, 7200][index%6]
        counters = json.dumps(dict(reads=100,sectors_read=1000,writes=200,sectors_written=2000,discards=0,sectors_discarded=0,flushes=0))
        sid = connection.execute("INSERT INTO observation_sessions(disk_name,started_at,last_observed_at,ended_at,boot_id,sample_interval_seconds,counters,last_activity_at,idle_started_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                 (name,start,now,now,"synthetic-boot",30,counters,start,start)).lastrowid
        previous = start
        t = start+gap
        while t < now:
            connection.execute("INSERT INTO idle_intervals(session_id,disk_name,started_at,ended_at,duration_seconds,start_is_censored) VALUES (?,?,?,?,?,0)", (sid,name,previous,t,gap))
            connection.execute("INSERT INTO activity_events(session_id,disk_name,observed_at,reads_delta,writes_delta,bytes_read_delta,bytes_written_delta,discards_delta,bytes_discarded_delta,flushes_delta) VALUES (?,?,?,1,0,4096,0,0,0,0)", (sid,name,t))
            previous=t
            t+=gap
        connection.execute("INSERT INTO idle_intervals(session_id,disk_name,started_at,ended_at,duration_seconds,start_is_censored,end_is_censored) VALUES (?,?,?,?,?,0,1)", (sid,name,previous,now,now-previous))

app = create_app(config)
app.state.demo = True

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
