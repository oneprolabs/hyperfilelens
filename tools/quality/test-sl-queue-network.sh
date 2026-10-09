#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${HFL_TEST_PYTHON:-python3}"
id="hfl-queue-check-$$"
manifest="$(mktemp)"
cleanup() {
  for c in "$id-client" "$id-hfl" "$id-sl"; do docker rm -fv "$c" >/dev/null 2>&1 || true; done
  for n in "$id-default" "$id-shared" "$id-private"; do docker network rm "$n" >/dev/null 2>&1 || true; done
  rm -f -- "$manifest"
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
docker run -d --pull never --name "$id-client" --network "$id-default" redis:alpine sleep 120 >/dev/null
docker network connect "$id-shared" "$id-client"
for i in $(seq 1 20); do
 if docker exec "$id-hfl" redis-cli PING >/dev/null && docker exec "$id-sl" redis-cli PING >/dev/null; then break; fi
 sleep 0.2
done
docker exec "$id-hfl" redis-cli SET origin hfl >/dev/null
docker exec "$id-sl" redis-cli LPUSH lens test-one test-two >/dev/null
docker exec "$id-sl" redis-cli LPUSH sourcelens test-three >/dev/null
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
CACHE_BACKEND=redis CACHE_REDIS_URL="redis://$hfl_ip:6379/1" HFL_SL_RUNTIME_QUEUES=lens,sourcelens HFL_SL_RUNTIME_QUEUE_WARNING=1 PYTHONPATH="$repo/src/backend" SL_TEST_URL="redis://$sl_ip:6379/0" "${python}" - <<'PY'
import os
from unittest.mock import patch
os.environ.setdefault('DJANGO_SETTINGS_MODULE','project.settings')
import django
django.setup()
from apps.instance_settings.services import sourcelens_runtime as runtime
url=os.environ['SL_TEST_URL']
with patch.object(runtime,'probe_queue_backlog',wraps=runtime.probe_queue_backlog) as query:
 first=runtime.cached_queue_backlog(url)
 second=runtime.cached_queue_backlog(url)
 assert first['health_status']=='ok',first
 assert first['queue_lengths']=={'lens':2,'sourcelens':1},first
 assert first==second
 query.assert_called_once()
print('Isolated real Redis: PING/LLEN, atomic cache write/release, and cached reuse passed.')
PY
docker network disconnect "$id-shared" "$id-sl"
[[ "$(docker exec "$id-client" redis-cli -h redis GET origin)" == hfl ]]
echo 'Isolated network disconnect preserved HFL Redis and the SL private network.'
