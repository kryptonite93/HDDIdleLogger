#!/bin/sh
set -eu
name="hdd-profiler-smoke-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$name" --cap-drop ALL --security-opt no-new-privileges:true \
  --mount "type=bind,src=$(pwd)/tests/fixtures/diskstats,dst=/host/proc/diskstats,readonly" \
  hdd-idle-profiler:test
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if docker exec "$name" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"; then
    break
  fi
  sleep 2
done
docker exec "$name" python -c "import os,json,urllib.request; assert os.getuid()!=0; disks=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/disks')); assert {d['device_name'] for d in disks}=={'sdb','sdc'}"
docker restart "$name"
sleep 3
docker exec "$name" python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/export/complete.json')); assert len(data['observation_sessions'])>=4"
