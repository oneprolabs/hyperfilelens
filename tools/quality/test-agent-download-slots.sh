#!/usr/bin/env bash
# Exercise the production Lua against a disposable Redis instance.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
python3 - "${ROOT}/src/backend/apps/node/services/internal/agent_download_slots.py" "${tmp}/acquire.lua" <<'PY'
import ast
import pathlib
import sys

tree = ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
script = next(
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "_ACQUIRE_LUA" for target in node.targets)
)
pathlib.Path(sys.argv[2]).write_text(script, encoding="utf-8")
PY

docker run --rm --network none --entrypoint sh \
    -v "${tmp}/acquire.lua:/tmp/acquire.lua:ro" \
    redis:alpine -ec '
redis-server --daemonize yes --save "" --appendonly no
for attempt in $(seq 1 30); do
    redis-cli ping >/dev/null 2>&1 && break
    sleep 0.1
done
key=hfl:agent-releases:slots:integration
acquire() { redis-cli --raw --eval /tmp/acquire.lua "$key" , "$1" 20 60; }
for number in $(seq 1 20); do
    result="$(acquire "session:$number")"
    [ "$result" = "$(printf "1\n%s" "$number")" ]
done
ttl_before="$(redis-cli pttl "$key")"
[ "$(acquire session:1)" = "$(printf "1\n20")" ]
[ "$(acquire session:21)" = "$(printf "0\n20")" ]
ttl_after="$(redis-cli pttl "$key")"
[ "$ttl_after" -le "$ttl_before" ]  # Neither reuse nor rejection renews TTL.
[ "$(redis-cli scard "$key")" = 20 ]
[ "$(redis-cli srem "$key" session:1)" = 1 ]
[ "$(acquire session:21)" = "$(printf "1\n20")" ]
legacy=hfl:agent-releases:slots:legacy
redis-cli sadd "$legacy" session:old >/dev/null
[ "$(redis-cli ttl "$legacy")" = -1 ]
[ "$(redis-cli --raw --eval /tmp/acquire.lua "$legacy" , session:new 20 60)" = "$(printf "1\n2")" ]
[ "$(redis-cli ttl "$legacy")" -gt 0 ]
printf "Agent download slot Lua checks passed.\n"
'
