#!/usr/bin/env bash
# Exercise the production Lua against a disposable Redis instance.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
python3 - "${ROOT}/src/backend/apps/node/services/internal/agent_download_slots.py" "${tmp}" <<'PY'
import ast
import pathlib
import sys

tree = ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for name, filename in (("_ACQUIRE_LUA", "acquire.lua"), ("_RELEASE_LUA", "release.lua")):
    script = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )
    (pathlib.Path(sys.argv[2]) / filename).write_text(script, encoding="utf-8")
PY

docker run --rm --network none --entrypoint sh \
    -v "${tmp}/acquire.lua:/tmp/acquire.lua:ro" \
    -v "${tmp}/release.lua:/tmp/release.lua:ro" \
    redis:alpine -ec '
redis-server --daemonize yes --save "" --appendonly no
for attempt in $(seq 1 30); do
    redis-cli ping >/dev/null 2>&1 && break
    sleep 0.1
done
key=hfl:agent-releases:slots:integration
deadlines="${key}:deadlines"
acquire() { redis-cli --raw --eval /tmp/acquire.lua "$key" "$deadlines" , "$1" 20 60; }
release() { redis-cli --raw --eval /tmp/release.lua "$key" "$deadlines" , "$1"; }
for number in $(seq 1 20); do
    result="$(acquire "session:$number")"
    [ "$result" = "$(printf "1\n%s" "$number")" ]
done
untouched_deadline="$(redis-cli zscore "$deadlines" session:2)"
[ "$(redis-cli zcard "$deadlines")" = 20 ]
[ "$(acquire session:1)" = "$(printf "1\n20")" ]
[ "$(redis-cli zscore "$deadlines" session:2)" = "$untouched_deadline" ]
[ "$(acquire session:21)" = "$(printf "0\n20")" ]
[ "$(redis-cli zscore "$deadlines" session:2)" = "$untouched_deadline" ]
[ "$(redis-cli scard "$key")" = 20 ]
[ "$(release session:1)" = 1 ]
[ "$(release session:1)" = 0 ]
[ -z "$(redis-cli zscore "$deadlines" session:1)" ]
[ "$(acquire session:21)" = "$(printf "1\n20")" ]
legacy=hfl:agent-releases:slots:legacy
legacy_deadlines="${legacy}:deadlines"
redis-cli sadd "$legacy" session:old >/dev/null
redis-cli expire "$legacy" 5 >/dev/null
[ "$(redis-cli --raw --eval /tmp/acquire.lua "$legacy" "$legacy_deadlines" , session:new 20 60)" = "$(printf "1\n2")" ]
[ "$(redis-cli zscore "$legacy_deadlines" session:old)" -lt "$(redis-cli zscore "$legacy_deadlines" session:new)" ]
[ "$(redis-cli ttl "$legacy")" -gt 0 ]
# Expire only the old member; a later member must remain counted.
redis-cli zadd "$legacy_deadlines" 1 session:old >/dev/null
[ "$(redis-cli --raw --eval /tmp/acquire.lua "$legacy" "$legacy_deadlines" , session:third 20 60)" = "$(printf "1\n2")" ]
[ "$(redis-cli sismember "$legacy" session:old)" = 0 ]
[ "$(redis-cli sismember "$legacy" session:new)" = 1 ]
full=hfl:agent-releases:slots:full
full_deadlines="${full}:deadlines"
for number in $(seq 1 20); do
    redis-cli --raw --eval /tmp/acquire.lua "$full" "$full_deadlines" , "session:$number" 20 60 >/dev/null
done
# The oldest authorization expires while 19 others remain valid. A new
# request must reclaim just that one place, not reset the entire group.
redis-cli zadd "$full_deadlines" 1 session:1 >/dev/null
[ "$(redis-cli --raw --eval /tmp/acquire.lua "$full" "$full_deadlines" , session:21 20 60)" = "$(printf "1\n20")" ]
[ "$(redis-cli sismember "$full" session:1)" = 0 ]
[ "$(redis-cli sismember "$full" session:2)" = 1 ]
[ "$(redis-cli sismember "$full" session:21)" = 1 ]
redis-cli del "$key" >/dev/null
[ "$(redis-cli --raw --eval /tmp/acquire.lua "$key" "$deadlines" , session:fresh 20 60)" = "$(printf "1\n1")" ]
[ "$(redis-cli zcard "$deadlines")" = 1 ]
printf "Agent download slot Lua checks passed.\n"
'
