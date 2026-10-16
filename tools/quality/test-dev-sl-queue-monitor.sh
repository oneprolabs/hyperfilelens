#!/usr/bin/env bash
# Offline dev wiring contract; never inspect or modify live Docker services.
set -euo pipefail
ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../dev/stack.sh
source "${ROOT_REPO}/dev/stack.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
ROOT="${tmp}/repo"
mkdir -p "${ROOT}/deploy/installer"
calls="${tmp}/calls"
warnings="${tmp}/warnings"
python3() {
	printf '%s\n' "$*" >>"${calls}"
	printf '%b' "${helper_output:-}"
	printf '%b' "${helper_error:-}" >&2
	return "${helper_status:-0}"
}
warn() {
	printf '%s\n' "$*" >>"${warnings}"
}

# Missing helper is best-effort and must not invoke Python.
WITH_SOURCELENS=1
configure_sl_queue_monitor_dev
[[ ! -e "${calls}" ]]
grep -F 'helper is unavailable' "${warnings}" >/dev/null

touch "${ROOT}/deploy/installer/configure-sl-queue-monitor.py"
configure_sl_queue_monitor_dev
grep -Fx "${ROOT}/deploy/installer/configure-sl-queue-monitor.py --root ${ROOT} --dev" \
	"${calls}" >/dev/null
helper_status=7
configure_sl_queue_monitor_dev
grep -F 'setup failed; core services are unaffected' "${warnings}" >/dev/null

HFL_LOG_TERMINAL_TIMESTAMPS=1
HFL_LOG_COLOR=0
helper_status=0
helper_output='[queue-monitor] bundled monitoring configuration reconciled\n'
configure_sl_queue_monitor_dev >"${tmp}/monitor.out" 2>&1
grep -E '^\[[0-9T:.-]+Z\] \[ OK \] \[queue-monitor\] bundled monitoring configuration reconciled$' \
	"${tmp}/monitor.out" >/dev/null

helper_output='[queue-config] SL defaults and worker subscriptions are consistent\n'
verify_sl_queue_config_dev >"${tmp}/verify.out" 2>&1
grep -E '^\[[0-9T:.-]+Z\] \[ OK \] \[queue-config\] SL defaults and worker subscriptions are consistent$' \
	"${tmp}/verify.out" >/dev/null
grep -Fx "${ROOT}/deploy/installer/configure-sl-queue-monitor.py --root ${ROOT} --dev --verify-queues --timeout 60" \
	"${calls}" >/dev/null

# Warning results remain non-fatal and must not be rendered as success.
helper_output='[queue-monitor] WARNING: unsafe automatic setup disabled; core services are unaffected\n'
configure_sl_queue_monitor_dev >"${tmp}/warning.out" 2>&1
grep -F '[WARN] [queue-monitor] unsafe automatic setup disabled; core services are unaffected' \
	"${tmp}/warning.out" >/dev/null
! grep -F '[ OK ]' "${tmp}/warning.out" >/dev/null

# Stderr is formatted too, and the read-only verification gate stays fatal.
helper_output=''
helper_error='[queue-config] ERROR: SL queue configuration could not be verified\n'
helper_status=7
status=0
(verify_sl_queue_config_dev) >"${tmp}/failure.out" 2>&1 || status=$?
[[ "${status}" -ne 0 ]]
grep -F '[FAIL] [queue-config] SL queue configuration could not be verified' \
	"${tmp}/failure.out" >/dev/null
! grep -F '[ OK ]' "${tmp}/failure.out" >/dev/null

helper_output='[queue-config] external SourceLens skipped\n'
helper_error=''
helper_status=0
verify_sl_queue_config_dev >"${tmp}/external.out" 2>&1
grep -F '[SKIP] [queue-config] external SourceLens skipped' \
	"${tmp}/external.out" >/dev/null

# Unknown diagnostic text is not promoted to a successful setup result.
printf '%s\n' '[queue-monitor] additional diagnostic detail' \
	| render_sl_queue_setup_stream >"${tmp}/diagnostic.out" 2>&1
grep -F '[OUT ] [queue-monitor] additional diagnostic detail' \
	"${tmp}/diagnostic.out" >/dev/null

# --no-sourcelens/--hfl-only must not reconcile or remove an existing setup.
WITH_SOURCELENS=0
before="$(wc -l <"${calls}")"
configure_sl_queue_monitor_dev
verify_sl_queue_config_dev
[[ "$(wc -l <"${calls}")" -eq "${before}" ]]
printf 'Development SourceLens queue-monitor wiring tests passed.\n'
