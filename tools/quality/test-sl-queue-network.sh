#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${HFL_TEST_PYTHON:-python3}"
id="hfl-queue-check-$$"
manifest="$(mktemp)"
runtime_dir="$(mktemp -d)"
cleanup() {
  for c in "$id-client" "$id-hfl" "$id-sl"; do docker rm -fv "$c" >/dev/null 2>&1 || true; done
  for n in "$id-default" "$id-shared" "$id-private"; do docker network rm "$n" >/dev/null 2>&1 || true; done
  rm -f -- "$manifest"
  rm -rf -- "$runtime_dir"
}
trap cleanup EXIT
for n in "$id-default" "$id-shared" "$id-private"; do docker network create "$n" >/dev/null; done
docker run -d --pull never --name "$id-hfl" --network "$id-default" --network-alias redis redis:alpine redis-server --save '' >/dev/null
cat >"$manifest" <<COMPOSE
services:
  redis:
    image: redis:alpine
    container_name: $id-sl
    command: ["redis-server", "--save", ""]
    networks: [private]
networks:
  private:
    external: true
    name: $id-private
COMPOSE
docker compose -p "$id-compose" -f "$manifest" up -d --no-build --pull never >/dev/null
sl_identity="$(docker inspect --format '{{.Id}}|{{.State.StartedAt}}' "$id-sl")"
docker run -d --pull never --name "$id-client" --network "$id-default" \
 --mount "type=bind,src=$runtime_dir,dst=/test-runtime,readonly" redis:alpine sleep 120 >/dev/null
client_identity="$(docker inspect --format '{{.Id}}|{{.State.StartedAt}}' "$id-client")"
docker network connect "$id-shared" "$id-client"
for i in $(seq 1 20); do
 if docker exec "$id-hfl" redis-cli PING >/dev/null && docker exec "$id-sl" redis-cli PING >/dev/null; then break; fi
 sleep 0.2
done
docker exec "$id-hfl" redis-cli SET origin hfl >/dev/null
docker exec "$id-sl" redis-cli LPUSH lens test-one test-two >/dev/null
docker exec "$id-sl" redis-cli LPUSH sourcelens test-three >/dev/null
docker exec "$id-sl" redis-cli -n 2 LPUSH lens next-db-one next-db-two next-db-three >/dev/null
before="$(docker exec "$id-client" redis-cli -h redis GET origin)"
docker network connect --alias hfl-sourcelens-redis "$id-shared" "$id-sl"
docker compose -p "$id-compose" -f "$manifest" up -d --no-build --pull never >/dev/null
[[ "$(docker inspect --format '{{.Id}}|{{.State.StartedAt}}' "$id-sl")" == "$sl_identity" ]]
echo 'Unchanged Compose convergence did not recreate/restart Redis after the extra attachment.'
after="$(docker exec "$id-client" redis-cli -h redis GET origin)"
[[ "$before" == hfl && "$after" == hfl ]]
[[ "$(docker exec "$id-client" redis-cli -h hfl-sourcelens-redis LLEN lens)" == 2 ]]
echo 'Isolated real Docker DNS: HFL redis unchanged; SL alias returns its own queue.'
hfl_ip="$(docker inspect --format "{{(index .NetworkSettings.Networks \"$id-default\").IPAddress}}" "$id-hfl")"
sl_ip="$(docker inspect --format "{{(index .NetworkSettings.Networks \"$id-private\").IPAddress}}" "$id-sl")"
CACHE_BACKEND=redis CACHE_REDIS_URL="redis://$hfl_ip:6379/1" HFL_SL_RUNTIME_QUEUES=lens,sourcelens HFL_SL_RUNTIME_QUEUE_WARNING=1 PYTHONPATH="$repo/src/backend" SL_TEST_URL="redis://$sl_ip:6379/0" SL_TEST_RUNTIME="$runtime_dir" SL_TEST_CLIENT="$id-client" "${python}" - <<'PY'
import os
import json
import subprocess
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('DJANGO_SETTINGS_MODULE','project.settings')
import django
django.setup()
from apps.instance_settings.services import sourcelens_runtime as runtime
url=os.environ['SL_TEST_URL']
config=Path(os.environ['SL_TEST_RUNTIME'])/'sl-queue-monitor.json'
def publish(value):
 tmp=config.with_suffix('.tmp')
 tmp.write_text(json.dumps({'version':1,'url':value}))
 tmp.chmod(0o600)
 tmp.replace(config)
 visible=json.loads(subprocess.check_output([
  'docker','exec',os.environ['SL_TEST_CLIENT'],'cat','/test-runtime/sl-queue-monitor.json',
 ],text=True))
 assert visible['url']==value
os.environ['HFL_SL_RUNTIME_AUTO_REDIS_URL']=url
publish(url)
with patch.object(runtime,'AUTO_QUEUE_CONFIG_PATH',config),patch.object(runtime,'probe_queue_backlog',wraps=runtime.probe_queue_backlog) as query:
 first=runtime.cached_queue_backlog(runtime.queue_monitor_url())
 second=runtime.cached_queue_backlog(runtime.queue_monitor_url())
 assert first['health_status']=='ok',first
 assert first['queue_lengths']=={'lens':2,'sourcelens':1},first
 assert first==second
 query.assert_called_once()
 publish(url.rsplit('/',1)[0]+'/2')
 updated=runtime.cached_queue_backlog(runtime.queue_monitor_url())
 assert updated['queue_lengths']=={'lens':3,'sourcelens':0},updated
 assert query.call_count==2
 assert os.environ['HFL_SL_RUNTIME_AUTO_REDIS_URL']==url
print('Isolated real Redis: PING/LLEN, atomic cache write/release, and cached reuse passed.')
print('Read-only directory mount and hot broker DB update worked without changing process env or restarting containers.')
PY
[[ "$(docker inspect --format '{{.Id}}|{{.State.StartedAt}}' "$id-client")" == "$client_identity" ]]
docker network disconnect "$id-shared" "$id-sl"
[[ "$(docker exec "$id-client" redis-cli -h redis GET origin)" == hfl ]]
echo 'Isolated network disconnect preserved HFL Redis and the SL private network.'
