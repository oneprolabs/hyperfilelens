#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT_REPO}/deploy/installer/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

ROOT="${tmp}/install"
mkdir -p \
	"${ROOT}/backup" \
	"${ROOT}/data/postgresql" \
	"${ROOT}/data/sourcelens/postgresql" \
	"${ROOT}/data/redis" \
	"${ROOT}/data/sourcelens/redis" \
	"${ROOT}/data/logs" \
	"${ROOT}/data/sourcelens/logs" \
	"${ROOT}/data/sourcelens/workspace"
printf 'config\n' >"${ROOT}/.env"
printf 'exclude\n' >"${ROOT}/data/postgresql/PG_VERSION"
printf 'exclude\n' >"${ROOT}/data/sourcelens/postgresql/PG_VERSION"
printf 'exclude\n' >"${ROOT}/data/redis/dump.rdb"
printf 'exclude\n' >"${ROOT}/data/logs/api.log"
printf 'retain\n' >"${ROOT}/data/sourcelens/workspace/document.txt"

step() { :; }
warn() { :; }
log() { :; }
die() { printf 'ERROR: %s\n' "$1" >&2; return "${2:-1}"; }
read_env_value() { :; }
read_version() { printf '0.1.7'; }
source <(sed -n '/^backup_env_and_data()/,/^apply_upgrade_files()/p' "${installer}" | sed '$d')

target="${ROOT}/backup/.partial-20260723-010000-test"
mkdir -p "${target}"
backup_env_and_data "${target}"
archive="${target}/config-and-data.tar.gz"
tar -tzf "${archive}" >"${tmp}/contents.txt"
grep -F 'data/sourcelens/workspace/document.txt' "${tmp}/contents.txt" >/dev/null
if grep -E 'data/(postgresql|redis|logs)|data/sourcelens/(postgresql|redis|logs)' \
	"${tmp}/contents.txt" >/dev/null; then
	printf 'ERROR: live database, cache, or log data entered the upgrade archive\n' >&2
	exit 1
fi

write_backup_manifest "${target}" 20260723-010000
python3 - "${target}/backup-manifest.json" <<'PY'
import json
import pathlib
import sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["complete"] is True
assert any(item["name"] == "config-and-data.tar.gz" for item in manifest["files"])
PY

rm -rf "${target}"
for stamp in 20260720-010000 20260721-010000 20260722-010000 20260723-010000; do
	mkdir -p "${ROOT}/backup/upgrade-${stamp}"
	printf '{"complete": true}\n' >"${ROOT}/backup/upgrade-${stamp}/backup-manifest.json"
done
transaction="${ROOT}/deploy/upgrades/$(printf a%.0s {1..64})"
mkdir -p "${transaction}"
protected="${ROOT}/backup/upgrade-20260720-010000"
cat >"${transaction}/state" <<EOF
artifact_sha256=$(printf a%.0s {1..64})
target_version=0.1.8
status=active
phase=backup_complete
backup_dir=${protected}
updated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
mkdir -p "${ROOT}/backup/.partial-stale"
touch -d '2 days ago' "${ROOT}/backup/.partial-stale"
prune_upgrade_backups

[[ -e "${ROOT}/backup/upgrade-20260720-010000" ]]
[[ -e "${ROOT}/backup/upgrade-20260721-010000" ]]
[[ -e "${ROOT}/backup/upgrade-20260722-010000" ]]
[[ -e "${ROOT}/backup/upgrade-20260723-010000" ]]
[[ ! -e "${ROOT}/backup/.partial-stale" ]]

# A stale active transaction must not pin its backup forever.
sed -i 's#^updated_at=.*#updated_at=2020-01-01T00:00:00Z#' \
	"${transaction}/state"
prune_upgrade_backups
[[ ! -e "${ROOT}/backup/upgrade-20260720-010000" ]]
printf 'Upgrade backup retention checks passed.\n'
