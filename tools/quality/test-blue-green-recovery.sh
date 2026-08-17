#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1090
source "${ROOT_REPO}/deploy/installer/install.sh"
ORIGINAL_WAIT_FOR_SOURCELENS_HEALTH="$(declare -f wait_for_sourcelens_health)"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
ROOT="${tmp}/install"
mkdir -p "${ROOT}"
printf 'APP_VERSION=1.0.0\n' >"${ROOT}/.env"

calls=()
sourcelens_present=0
compose_in_root() { calls+=("compose:$*"); }
compose_color() { calls+=("color:$*"); }
render_active_upstreams() { calls+=("render:$*"); }
reload_stable_nginx() { calls+=("reload"); }
write_active_color() { calls+=("active:$*"); }
wait_for_public_endpoints() { calls+=("public-health"); }
wait_for_color_health() { calls+=("color-health:$*"); }
wait_for_services_health() { calls+=("service-health:$*"); }
ensure_blue_green_state() { calls+=("ensure-state"); }
read_active_color() { printf 'blue'; }
sourcelens_installed() { [[ "${sourcelens_present}" == "1" ]]; }
sourcelens_compose() { calls+=("sourcelens:$*"); }
wait_for_sourcelens_health() {
	calls+=("sourcelens-health")
	[[ "${sourcelens_health_ok:-1}" == "1" ]]
}
ok() { :; }
warn() { :; }
resolve_console_host() { printf '127.0.0.1'; }

pin_gateway_version_if_missing 1.0.0
grep -Fx 'HFL_GATEWAY_VERSION=1.0.0' "${ROOT}/.env" >/dev/null
pin_gateway_version_if_missing 2.0.0
grep -Fx 'HFL_GATEWAY_VERSION=1.0.0' "${ROOT}/.env" >/dev/null

mkdir -p "${ROOT}/sourcelens"
printf '%s\n' '{"git_ref":"v0.20.0"}' \
	>"${ROOT}/sourcelens/BUILD_INFO.json"
printf '%s\n' 'SOURCELENS_GIT_REF=v0.4.0' 'FRONTEND_URL=https://app.example.invalid' \
	>>"${ROOT}/.env"
configure_lens_bridge_env
grep -Fx 'SOURCELENS_GIT_REF=v0.20.0' "${ROOT}/.env" >/dev/null
grep -Fx 'LENS_BASE_URL=http://sourcelens-nginx' "${ROOT}/.env" >/dev/null
grep -Fx 'LENS_GATEWAY_BASE_URL=https://app.example.invalid/sourcelens' \
	"${ROOT}/.env" >/dev/null

calls=()
start_hfl_stack
[[ " ${calls[*]} " == *" compose:--profile tools run --rm --no-deps migration "* ]]
[[ " ${calls[*]} " != *" run --rm --no-deps --pull never migration "* ]]
[[ " ${calls[*]} " == *" color:blue up -d --no-build --pull never api-blue web-blue "* ]]
[[ " ${calls[*]} " == *" color-health:blue "* ]]
[[ " ${calls[*]} " == *" compose:up -d --no-build --pull never nginx "* ]]
[[ " ${calls[*]} " == *" service-health:600 nginx "* ]]
[[ " ${calls[*]} " != *" reload "* ]]

calls=()
UPGRADE_HFL_WAS_RUNNING=1
UPGRADE_SOURCELENS_WAS_RUNNING=0
UPGRADE_PREVIOUS_COLOR=blue
UPGRADE_TARGET_COLOR=green
UPGRADE_HFL_COMMITTED=0
recover_upgrade_services
[[ " ${calls[*]} " == *" render:blue "* ]]
[[ " ${calls[*]} " == *" active:blue "* ]]
[[ " ${calls[*]} " != *" active:green "* ]]
[[ " ${calls[*]} " == *" compose:start worker scheduler "* ]]
[[ " ${calls[*]} " != *" compose:up -d --no-build --pull never worker scheduler "* ]]

calls=()
UPGRADE_HFL_COMMITTED=1
recover_upgrade_services
[[ " ${calls[*]} " == *" render:green "* ]]
[[ " ${calls[*]} " == *" active:green "* ]]
[[ " ${calls[*]} " == *" compose:up -d --no-build --pull never worker scheduler "* ]]

calls=()
UPGRADE_HFL_CUTOVER_ATTEMPTED=1
restore_previous_hfl_color blue green
[[ " ${calls[*]} " == *" render:blue "* ]]
[[ " ${calls[*]} " == *" compose:exec -T api-blue python manage.py ws_recovery_gate reattach "* ]]
[[ " ${calls[*]} " == *" compose:stop api-green web-green "* ]]
[[ " ${calls[*]} " == *" active:blue "* ]]

calls=()
UPGRADE_HFL_CUTOVER_ATTEMPTED=0
restore_previous_hfl_color legacy green
[[ " ${calls[*]} " == *" render:legacy green "* ]]
[[ " ${calls[*]} " == *" compose:stop api-green "* ]]
[[ " ${calls[*]} " != *" compose:stop api-green web-green "* ]]

calls=()
UPGRADE_HFL_WAS_RUNNING=0
UPGRADE_SOURCELENS_WAS_RUNNING=1
sourcelens_present=1
SOURCELENS_UPGRADE_STARTED=0
recover_upgrade_services
[[ " ${calls[*]} " == *" sourcelens:start "* ]]
[[ " ${calls[*]} " != *" sourcelens:up -d --no-build "* ]]

calls=()
SOURCELENS_UPGRADE_STARTED=1
recover_upgrade_services
[[ " ${calls[*]} " == *" sourcelens:up -d --no-build "* ]]

calls=()
SOURCELENS_MAINTENANCE_ARMED=1
sourcelens_health_ok=1
recover_upgrade_services
[[ " ${calls[*]} " == *" sourcelens-health "* ]]

calls=()
sourcelens_health_ok=0
if recover_upgrade_services; then
	printf 'ERROR: unhealthy SourceLens recovery must fail closed\n' >&2
	exit 1
fi
[[ " ${calls[*]} " == *" sourcelens-health "* ]]

SOURCELENS_INSTALL_DIR="${tmp}/sourcelens"
mkdir -p "${SOURCELENS_INSTALL_DIR}/deploy/nginx/hfl-maintenance"
sourcelens_nginx_running() { return 0; }
sourcelens_nginx_has_proxy_gate() { return 0; }
reload_sourcelens_proxy_gate() { calls+=("sourcelens-gate-reload"); }

calls=()
arm_sourcelens_proxy_gate
grep -F '"~^POST:/api/lens/sessions/' "$(sourcelens_proxy_gate_path)" >/dev/null
[[ " ${calls[*]} " == *" sourcelens-gate-reload "* ]]
[[ "${SOURCELENS_PROXY_GATE_ARMED}" == "1" ]]

calls=()
clear_sourcelens_proxy_gate
cmp -s "$(sourcelens_proxy_gate_path)" \
	"${ROOT_REPO}/deploy/installer/sourcelens/run-creation-gate-off.conf"
if grep -Fq '"~^POST:/api/lens/sessions/' "$(sourcelens_proxy_gate_path)"; then
	printf 'ERROR: clearing the SourceLens proxy gate left its blocking rule armed\n' >&2
	exit 1
fi
[[ " ${calls[*]} " == *" sourcelens-gate-reload "* ]]
[[ "${SOURCELENS_PROXY_GATE_ARMED}" == "0" ]]

# A pre-gate SourceLens release must adopt the guard by replacing only Nginx.
# The old proxy is stopped before the replacement starts, so direct Run
# creation stays fail-closed throughout the one-time adoption window.
SOURCELENS_GATE_ADOPTION_SOURCE="${tmp}/target-sourcelens"
mkdir -p "${SOURCELENS_GATE_ADOPTION_SOURCE}/deploy/nginx"
printf '%s\n' 'if ($hfl_sourcelens_run_creation_blocked) { return 503; }' \
	>"${SOURCELENS_GATE_ADOPTION_SOURCE}/deploy/nginx/default.conf"
legacy_gate_present=0
sourcelens_nginx_has_proxy_gate() { [[ "${legacy_gate_present}" == "1" ]]; }
sourcelens_compose() {
	calls+=("sourcelens:$*")
	if [[ " $* " == *" --force-recreate nginx "* ]]; then
		legacy_gate_present=1
	fi
}
calls=()
arm_sourcelens_proxy_gate
[[ " ${calls[*]} " == *" sourcelens:-f docker-compose.yml -f "*"adoption-compose.yml up -d --no-deps --no-build --pull never --force-recreate nginx "* ]]
grep -F 'adoption-default.conf:/etc/nginx/conf.d/default.conf:ro' \
	"${SOURCELENS_INSTALL_DIR}/deploy/nginx/hfl-maintenance/adoption-compose.yml" >/dev/null
[[ " ${calls[*]} " == *" sourcelens-gate-reload "* ]]
[[ "${legacy_gate_present}" == "1" ]]
clear_sourcelens_proxy_gate

# A failed reload and restart must retain the armed marker so the installer
# exit trap retries the live refresh. A later successful retry clears it.
restart_should_fail=1
reload_sourcelens_proxy_gate() { return 1; }
sourcelens_compose() {
	calls+=("sourcelens:$*")
	if [[ " $* " == *" restart nginx "* && "${restart_should_fail}" == "1" ]]; then
		return 1
	fi
	return 0
}
SOURCELENS_PROXY_GATE_ARMED=1
calls=()
if clear_sourcelens_proxy_gate; then
	printf 'ERROR: failed SourceLens gate refresh reported success\n' >&2
	exit 1
fi
[[ "${SOURCELENS_PROXY_GATE_ARMED}" == "1" ]]
[[ " ${calls[*]} " == *" sourcelens:restart nginx "* ]]
restart_should_fail=0
clear_sourcelens_proxy_gate
[[ "${SOURCELENS_PROXY_GATE_ARMED}" == "0" ]]

# Bundled mode is healthy only when every required service exists and is
# running/healthy, and the HTTPS endpoint is reachable.
eval "${ORIGINAL_WAIT_FOR_SOURCELENS_HEALTH}"
sourcelens_present=1
missing_service=""
unhealthy_service=""
health_endpoint_ok=1
frontend_service=web
SOURCELENS_HEALTH_TIMEOUT_SECONDS=1
configured_sourcelens_mode() { printf 'bundled'; }
read_env_value() { [[ "$1" == "SOURCELENS_CONSOLE_PORT" ]] && printf '11445'; }
sourcelens_compose() {
	if [[ "${1:-}" == "config" && "${2:-}" == "--services" ]]; then
		printf '%s\n' api "${frontend_service}" worker scheduler postgres redis nginx
		return 0
	fi
	if [[ "${1:-}" == "ps" && "${2:-}" == "-q" ]]; then
		local service="${3:-}"
		[[ "${service}" == "${missing_service}" ]] || printf '%s-cid\n' "${service}"
	fi
}
container_health_status() {
	local service="${1%-cid}"
	if [[ "${service}" == "${unhealthy_service}" ]]; then
		printf 'restarting'
	elif [[ "${service}" == "api" || "${service}" == "postgres" || "${service}" == "redis" ]]; then
		printf 'healthy'
	else
		printf 'running'
	fi
}
curl() { [[ "${health_endpoint_ok}" == "1" ]]; }
sleep() { SECONDS=$((SECONDS + 5)); }

wait_for_sourcelens_health
for missing_service in api web worker scheduler postgres redis nginx; do
	if wait_for_sourcelens_health; then
		printf 'ERROR: missing SourceLens service passed health: %s\n' "${missing_service}" >&2
		exit 1
	fi
done
frontend_service=ui
missing_service=ui
if wait_for_sourcelens_health; then
	printf 'ERROR: missing legacy SourceLens UI service passed health\n' >&2
	exit 1
fi
frontend_service=web
missing_service=""
unhealthy_service=worker
if wait_for_sourcelens_health; then
	printf 'ERROR: restarting SourceLens worker passed health\n' >&2
	exit 1
fi
unhealthy_service=""
health_endpoint_ok=0
if wait_for_sourcelens_health; then
	printf 'ERROR: unreachable SourceLens HTTPS endpoint passed health\n' >&2
	exit 1
fi
health_endpoint_ok=1
sourcelens_present=0
if wait_for_sourcelens_health; then
	printf 'ERROR: missing bundled SourceLens runtime passed health\n' >&2
	exit 1
fi

# A failed stop must abort the independent SourceLens upgrade before target
# runtime files or containers are converged over the still-running release.
sourcelens_present=1
sourcelens_down_ok=0
sourcelens_compose() {
	[[ "$*" != "down" || "${sourcelens_down_ok}" == "1" ]]
}
if stop_bundled_sourcelens; then
	printf 'ERROR: failed SourceLens shutdown reported success\n' >&2
	exit 1
fi
sourcelens_down_ok=1
stop_bundled_sourcelens

# Rebuilt SourceLens images must be deployable even when the upstream release
# and HFL functional Patch Series remain unchanged.
mkdir -p "${tmp}/bundle-current" "${tmp}/bundle-target"
printf '%s\n' \
	'{"git_ref":"v0.20.0","git_commit":"0123456789abcdef","version":"0.20.0","patchset_sha256":"patches","patches":[],"build_adapter_sha256":"adapter-old","build_compose_file":"docker-compose.standalone.yml"}' \
	>"${tmp}/bundle-current/BUILD_INFO.json"
printf '%s\n' \
	'{"git_ref":"v0.20.0","git_commit":"0123456789abcdef","version":"0.20.0","patchset_sha256":"patches","patches":[],"build_adapter_sha256":"adapter-new","build_compose_file":"docker-compose.standalone.yml"}' \
	>"${tmp}/bundle-target/BUILD_INFO.json"
current_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-current")"
target_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-target")"
if [[ "${current_fingerprint}" == "${target_fingerprint}" ]]; then
	printf 'ERROR: SourceLens build adapter change did not invalidate the runtime bundle\n' >&2
	exit 1
fi

# HFL distribution tags are transport aliases, not SourceLens semantics. Keep
# an unchanged SourceLens runtime online across ordinary HFL releases while
# still detecting real Compose contract changes.
cp "${tmp}/bundle-current/BUILD_INFO.json" "${tmp}/bundle-target/BUILD_INFO.json"
printf '%s\n' \
	'services:' \
	'  api:' \
	'    image: hyperfilelens-sourcelens-backend:0.1.8-sl0.20.0' \
	'    mem_limit: 512m' \
	>"${tmp}/bundle-current/docker-compose.yml"
printf '%s\n' \
	'services:' \
	'  api:' \
	'    image: hyperfilelens-sourcelens-backend:0.1.9-sl0.20.0' \
	'    mem_limit: 512m' \
	>"${tmp}/bundle-target/docker-compose.yml"
current_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-current")"
target_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-target")"
if [[ "${current_fingerprint}" != "${target_fingerprint}" ]]; then
	printf 'ERROR: HFL distribution tag caused an unnecessary SourceLens upgrade\n' >&2
	exit 1
fi
sed -i 's/mem_limit: 512m/mem_limit: 768m/' \
	"${tmp}/bundle-target/docker-compose.yml"
target_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-target")"
if [[ "${current_fingerprint}" == "${target_fingerprint}" ]]; then
	printf 'ERROR: SourceLens Compose contract change was ignored\n' >&2
	exit 1
fi

# Environment-rendered observability config is not bundle identity, while its
# renderer script is a static runtime adapter that must trigger convergence.
sed -i 's/mem_limit: 768m/mem_limit: 512m/' \
	"${tmp}/bundle-target/docker-compose.yml"
mkdir -p "${tmp}/bundle-current/deploy/nginx" \
	"${tmp}/bundle-target/deploy/nginx"
printf '%s\n' 'window.SENTRY = { enabled: true };' \
	>"${tmp}/bundle-current/deploy/nginx/hfl-sentry-config.js"
printf '%s\n' 'window.SENTRY = { enabled: false };' \
	>"${tmp}/bundle-target/deploy/nginx/hfl-sentry-config.js"
printf '%s\n' '#!/usr/bin/env python3' \
	>"${tmp}/bundle-current/sync-sentry-runtime.py"
cp "${tmp}/bundle-current/sync-sentry-runtime.py" \
	"${tmp}/bundle-target/sync-sentry-runtime.py"
current_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-current")"
target_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-target")"
if [[ "${current_fingerprint}" != "${target_fingerprint}" ]]; then
	printf 'ERROR: rendered SourceLens Sentry config changed bundle identity\n' >&2
	exit 1
fi
printf '%s\n' '# static adapter change' \
	>>"${tmp}/bundle-target/sync-sentry-runtime.py"
target_fingerprint="$(sourcelens_bundle_fingerprint "${tmp}/bundle-target")"
if [[ "${current_fingerprint}" == "${target_fingerprint}" ]]; then
	printf 'ERROR: SourceLens Sentry runtime adapter change was ignored\n' >&2
	exit 1
fi

printf 'Blue/green recovery state checks passed.\n'
