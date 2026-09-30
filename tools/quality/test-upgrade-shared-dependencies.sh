#!/usr/bin/env bash
# Exercise Compose dependency isolation with a disposable project.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
project="hfl-upgrade-deps-$$"
cleanup() {
	docker compose -p "${project}" -f "${tmp}/docker-compose.yml" down \
		--remove-orphans >/dev/null 2>&1 || true
	rm -rf -- "${tmp}"
}
trap cleanup EXIT

cat >"${tmp}/docker-compose.yml" <<'COMPOSE'
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_PASSWORD: isolated-test-password
      TEST_CONFIG: ${STATEFUL_CONFIG:-original}
  redis:
    image: redis:alpine
    command: ["redis-server", "--save", ""]
  api:
    image: redis:alpine
    command: ["sleep", "300"]
    depends_on:
      - postgres
      - redis
  worker:
    image: redis:alpine
    command: ["sleep", "300"]
    depends_on:
      - postgres
      - redis
  nginx:
    image: redis:alpine
    command: ["sleep", "300"]
    depends_on:
      - postgres
      - redis
COMPOSE

compose() { docker compose -p "${project}" -f "${tmp}/docker-compose.yml" "$@"; }
identity() {
	local service=$1 cid
	cid="$(compose ps -q "${service}")"
	[[ -n "${cid}" ]]
	docker inspect --format '{{.Id}}|{{.State.StartedAt}}' "${cid}"
}

compose up -d --no-build --pull never postgres redis >/dev/null
postgres_before="$(identity postgres)"
redis_before="$(identity redis)"

# Change a stateful service's desired Compose config while both are running.
# API, Worker, and edge convergence must not apply that change implicitly.
export STATEFUL_CONFIG=updated
compose up -d --no-deps --no-build --pull never api >/dev/null
compose up -d --no-deps --no-build --pull never worker >/dev/null
compose up -d --no-deps --no-build --pull never nginx >/dev/null
[[ "$(identity postgres)" == "${postgres_before}" ]]
[[ "$(identity redis)" == "${redis_before}" ]]

# App version alone must not change the pinned stable gateway's Compose hash.
printf 'APP_VERSION=0.2.28\nHFL_GATEWAY_VERSION=main-pinned\n' \
	>"${tmp}/old.env"
printf 'APP_VERSION=0.2.29\nHFL_GATEWAY_VERSION=main-pinned\n' \
	>"${tmp}/new.env"
old_hash="$(docker compose --env-file "${tmp}/old.env" \
	-f "${ROOT}/deploy/docker-compose.yml" config --hash nginx)"
new_hash="$(docker compose --env-file "${tmp}/new.env" \
	-f "${ROOT}/deploy/docker-compose.yml" config --hash nginx)"
[[ "${old_hash}" == "${new_hash}" ]]

printf 'Application upgrade Compose dependency isolation checks passed.\n'
