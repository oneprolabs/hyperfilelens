#!/usr/bin/env bash
# No real Docker, database, network, or installed paths are used by this test.
set -euo pipefail
ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1090
source "${ROOT_REPO}/deploy/installer/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
ROOT="${tmp}/install"
mkdir -p "${ROOT}/deploy" "${ROOT}/backup/upgrade-20261020-010000"
printf 'APP_VERSION=1.2.3\n' >"${ROOT}/.env"
calls="${tmp}/calls"
: >"${calls}"
UPGRADE_ARTIFACT_SHA256="$(printf a%.0s {1..64})"
UPGRADE_TARGET_VERSION=1.2.3
UPGRADE_PREVIOUS_COLOR=blue
UPGRADE_TARGET_COLOR=green
HFL_HTTP_DRAIN_SECONDS=0
UPGRADE_BACKUP_DIR="${ROOT}/backup/upgrade-20261020-010000"
old_cid="$(printf b%.0s {1..64})"
legacy_cid="$(printf c%.0s {1..64})"
writers_running=1
migration_status=0
database_policy=exclusive
legacy_running=false
orphan_running=0
migration_running=0
log() { :; }
warn() { :; }
ok() { :; }
docker() {
	printf 'docker:%s\n' "$*" >>"${calls}"
	case "$*" in
	"ps -a -q --filter id="*) printf '%s\n' "${*: -1}" ;;
	*"{{.Config.Hostname}}"*) printf 'old-ws-instance\n' ;;
	*"{{.State.Running}}"*) printf '%s\n' "${legacy_running}" ;;
	*"com.docker.compose.service"*)
		[[ "${*: -1}" != "${legacy_cid}" ]] || { printf 'api\n'; return; }
		[[ "${*: -1}" != orphan-api ]] || { printf 'api\n'; return; }
		[[ "${*: -1}" != pending-migration ]] || { printf 'migration\n'; return; }
		printf 'postgres\n'
		;;
	*"com.docker.compose.oneoff"*) printf '%s\n' "${legacy_oneoff:-False}" ;;
	"start "*) legacy_running=true ;;
	"stop --time 90 orphan-api") orphan_running=0 ;;
	"stop --time 90 "*) legacy_running=false ;;
	*) printf 'Unexpected Docker call: %s\n' "$*" >&2; return 1 ;;
	esac
}
compose_in_root() {
	printf 'compose:%s\n' "$*" >>"${calls}"
	case "$*" in
	"config --services") printf '%s\n' postgres redis nginx api-blue api-green worker scheduler ;;
	"ps -a -q api-blue") printf '%s\n' "${old_cid}" ;;
	"ps -a -q") printf '%s' "${retained_legacy:-}" ;;
	"ps -a --status running -q")
		[[ "${orphan_running}" == 0 ]] || printf 'orphan-api\n'
		[[ "${migration_running}" == 0 ]] || printf 'pending-migration\n'
		return 0
		;;
	"ps --status running -q "*) [[ "${writers_running}" == 0 ]] || printf 'old-writer\n' ;;
	"stop --timeout 90 "*) writers_running=0 ;;
	"stop api-green" | "rm -f api-green") : ;;
	"exec -T postgres sh -c "*) [[ "${database_policy}" != error ]] || return 1; printf '%s\n' "${database_policy}" ;;
	*"HFL_EXCLUSIVE_PLATFORM_MIGRATION=1 migration")
		[[ "${writers_running}" == 0 ]]
		[[ "$(read_exclusive_migration_state stage)" == migration_started ]]
		return "${migration_status}"
		;;
	"--profile tools run --rm --no-deps migration") return "${migration_status}" ;;
	"start "* | "up "* ) : ;;
	*) printf 'Unexpected Compose call: %s\n' "$*" >&2; return 1 ;;
	esac
}
compose_color() { printf 'color:%s\n' "$*" >>"${calls}"; }
ensure_stable_nginx_container() { printf 'ensure-edge\n' >>"${calls}"; }
render_active_upstreams() { printf 'route:%s\n' "$*" >>"${calls}"; }
reload_stable_nginx() { :; }
wait_for_public_endpoints() { [[ "${public_health:-1}" == 1 ]]; }
wait_for_active_task_reattach() {
	printf 'reattach:%s:%s\n' "$1" "${DRAINED_WS_INSTANCES}" >>"${calls}"
	[[ "${reattach_health:-1}" == 1 ]]
}
remove_retired_color() { printf 'retire:%s\n' "$1" >>"${calls}"; }
write_active_color() { printf 'active:%s\n' "$1" >>"${calls}"; }
configure_sl_queue_monitor() { :; }
sourcelens_installed() { return 1; }

# Compatibility detection is read-only and fails closed on database errors.
platform_ownership_migration_pending
database_policy=compatible
if platform_ownership_migration_pending; then exit 1; fi
database_policy=error
if (platform_ownership_migration_pending) >/dev/null 2>&1; then exit 1; fi
! grep -q 'compose:stop' "${calls}"
database_policy=exclusive

# A prepared fence is durable but permits recovery before any migration starts.
UPGRADE_EXCLUSIVE_REQUIRED=1
write_exclusive_migration_state prepared
[[ "$(stat -c '%a' "$(exclusive_migration_state_file)")" == 600 ]]
if exclusive_migration_started; then exit 1; fi
if (run_upgrade_database_migration) >/dev/null 2>&1; then exit 1; fi
[[ "$(read_exclusive_migration_state stage)" == prepared ]]
! grep -q 'HFL_EXCLUSIVE_PLATFORM_MIGRATION=1 migration' "${calls}"

# All application/edge writers stop; PostgreSQL and Redis are never stopped.
stop_exclusive_application_writers
[[ "${DRAINED_WS_INSTANCES}" == old-ws-instance ]]
grep -F 'compose:stop --timeout 90 nginx api-blue api-green worker scheduler' "${calls}"
! grep -E 'compose:stop .* (postgres|redis)' "${calls}"

# Failure to durably record the boundary must never approve a migration, even
# when the caller uses an if/|| context (where Bash disables implicit errexit).
if (
	write_exclusive_migration_state() { return 1; }
	run_upgrade_database_migration
) >/dev/null 2>&1; then exit 1; fi
[[ "$(read_exclusive_migration_state stage)" == prepared ]]
! grep -q 'HFL_EXCLUSIVE_PLATFORM_MIGRATION=1 migration' "${calls}"

# A failed command can have committed some migrations: retain the fence.
migration_status=42
if (run_upgrade_database_migration); then exit 1; fi
[[ "$(read_exclusive_migration_state stage)" == migration_started ]]
UPGRADE_EXCLUSIVE_MIGRATION_STARTED=0 # Simulate a new installer process.
exclusive_migration_started
if (write_exclusive_migration_state prepared) >/dev/null 2>&1; then exit 1; fi
: >"${calls}"
UPGRADE_HFL_WAS_RUNNING=1
UPGRADE_HFL_COMMITTED=0
if recover_upgrade_services; then exit 1; fi
! grep -E 'color:.*start|compose:start|route:' "${calls}"
if restore_previous_hfl_color blue green; then exit 1; fi
if (start_hfl_stack) >/dev/null 2>&1; then exit 1; fi
if (restart_language_pack_services) >/dev/null 2>&1; then exit 1; fi
[[ -f "$(exclusive_migration_state_file)" ]]

# A different package must not replace the original recovery point.
original_sha="${UPGRADE_ARTIFACT_SHA256}"
UPGRADE_ARTIFACT_SHA256="$(printf d%.0s {1..64})"
if (resume_exclusive_migration_state) >/dev/null 2>&1; then exit 1; fi
UPGRADE_ARTIFACT_SHA256="${original_sha}"
UPGRADE_PREVIOUS_COLOR=green
UPGRADE_TARGET_COLOR=blue
UPGRADE_EXCLUSIVE_REQUIRED=0
resume_exclusive_migration_state
[[ "${UPGRADE_PREVIOUS_COLOR}" == blue && "${UPGRADE_TARGET_COLOR}" == green ]]
[[ "${UPGRADE_EXCLUSIVE_REQUIRED}" == 1 && "${UPGRADE_EXCLUSIVE_MIGRATION_STARTED}" == 1 ]]

# An unhealthy candidate cannot restore old traffic after the data boundary.
: >"${calls}"
public_health=0
if cutover_hfl_color blue green; then exit 1; fi
! grep -q 'route:blue' "${calls}"
[[ -f "$(exclusive_migration_state_file)" ]]
public_health=1
migration_status=0
run_upgrade_database_migration
reattach_health=0
if cutover_hfl_color blue green; then exit 1; fi
! grep -q 'route:blue' "${calls}"
reattach_health=1
cutover_hfl_color blue green
grep -F 'reattach:green:old-ws-instance' "${calls}"
UPGRADE_HFL_COMMITTED=1
clear_exclusive_migration_state
[[ ! -e "$(exclusive_migration_state_file)" ]]

# Legacy singleton API identities survive a stopped/retried upgrade.
retained_legacy="${legacy_cid}"
legacy_oneoff=True
[[ -z "$(find_legacy_api_container)" ]]
legacy_oneoff=False
[[ "$(find_legacy_api_container)" == "${legacy_cid}" ]]
UPGRADE_EXCLUSIVE_MIGRATION_STARTED=0
UPGRADE_HFL_COMMITTED=0
UPGRADE_PREVIOUS_COLOR=legacy
UPGRADE_LEGACY_API_CID="${legacy_cid}"
legacy_running=true
write_exclusive_migration_state prepared
UPGRADE_LEGACY_API_CID=""
resume_exclusive_migration_state
[[ "${UPGRADE_LEGACY_API_CID}" == "${legacy_cid}" ]]
stop_exclusive_application_writers
[[ "${legacy_running}" == false ]]
UPGRADE_HFL_CUTOVER_ATTEMPTED=0
recover_upgrade_services
[[ "${legacy_running}" == true ]]
[[ ! -e "$(exclusive_migration_state_file)" ]]

# Before migration, ordinary recovery remains available and clears preparation.
UPGRADE_PREVIOUS_COLOR=blue
UPGRADE_LEGACY_API_CID=""
UPGRADE_HFL_CUTOVER_ATTEMPTED=0
write_exclusive_migration_state prepared
recover_upgrade_services
grep -F 'color:blue start api-blue web-blue' "${calls}"
[[ ! -e "$(exclusive_migration_state_file)" ]]

# The compatible branch keeps the old Blue/Green migration command unchanged.
UPGRADE_EXCLUSIVE_REQUIRED=0
: >"${calls}"
run_upgrade_database_migration
grep -F 'compose:--profile tools run --rm --no-deps migration' "${calls}"
! grep -q 'HFL_EXCLUSIVE_PLATFORM_MIGRATION' "${calls}"

# Orphan singleton APIs stop too; a still-running one-off migration must finish,
# rather than being killed or raced by another migration.
orphan_running=1
stop_exclusive_application_writers
[[ "${orphan_running}" == 0 ]]
migration_running=1
if verify_exclusive_application_stopped; then exit 1; fi
if stop_exclusive_application_writers; then exit 1; fi
! grep -q 'docker:stop --time 90 pending-migration' "${calls}"
migration_running=0

# Verify integration ordering, not only the helper functions.
body="$(declare -f cmd_upgrade)"
stop_line="$(grep -nF 'stop_exclusive_application_writers' <<<"${body}" | cut -d: -f1)"
migration_line="$(grep -nF 'run_upgrade_database_migration' <<<"${body}" | cut -d: -f1)"
candidate_line="$(grep -nF 'wait_for_color_health' <<<"${body}" | cut -d: -f1)"
commit_line="$(grep -nF 'UPGRADE_HFL_COMMITTED=1' <<<"${body}" | cut -d: -f1)"
clear_line="$(grep -nF 'clear_exclusive_migration_state' <<<"${body}" | cut -d: -f1)"
((stop_line < migration_line && migration_line < candidate_line && commit_line < clear_line))
printf 'Exclusive platform ownership upgrade safety checks passed.\n'
