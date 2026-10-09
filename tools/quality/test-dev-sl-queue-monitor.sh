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

# --no-sourcelens/--hfl-only must not reconcile or remove an existing setup.
WITH_SOURCELENS=0
before="$(wc -l <"${calls}")"
configure_sl_queue_monitor_dev
[[ "$(wc -l <"${calls}")" -eq "${before}" ]]
printf 'Development SourceLens queue-monitor wiring tests passed.\n'
