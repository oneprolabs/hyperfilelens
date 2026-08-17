#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1090
source "${ROOT_REPO}/deploy/installer/install.sh"
ORIGINAL_RELOAD_STABLE_NGINX="$(declare -f reload_stable_nginx)"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
ROOT="${tmp}/install"
mkdir -p "${ROOT}"
printf 'APP_VERSION=1.0.0\n' >"${ROOT}/.env"

calls=()
nginx_generation=""
nginx_generation_after_up=""
nginx_generation_status=0
nginx_generation_status_after_up=0
compose_failure_pattern=""

compose_in_root() {
	calls+=("compose:$*")
	if [[ -n "${compose_failure_pattern}" && " $* " == *" ${compose_failure_pattern} "* ]]; then
		return 1
	fi
	if [[ " $* " == *" up -d --no-build --pull never nginx "* ]]; then
		nginx_generation="${nginx_generation_after_up}"
		nginx_generation_status="${nginx_generation_status_after_up}"
	fi
}
compose_color() { calls+=("color:$*"); }
ensure_blue_green_state() { calls+=("ensure-state"); }
read_active_color() { printf 'blue'; }
stable_nginx_running_generation() {
	[[ "${nginx_generation_status}" -eq 0 ]] || return "${nginx_generation_status}"
	printf '%s' "${nginx_generation}"
}
wait_for_color_health() { calls+=("color-health:$*"); }
wait_for_services_health() { calls+=("service-health:$*"); }
reload_stable_nginx() { calls+=("reload"); }
log() { :; }

# A new stable gateway reads the current configuration during process startup.
# Reloading it immediately is both redundant and racy because nginx.pid may not
# have been populated yet.
nginx_generation=""
nginx_generation_after_up="new-container|started-now"
nginx_generation_status=0
nginx_generation_status_after_up=0
start_hfl_stack
[[ " ${calls[*]} " != *" reload "* ]]
[[ " ${calls[*]} " == *" service-health:600 nginx "* ]]

# A failed Compose start must stop the function before instance inspection,
# reload, or health checks. start_hfl_stack is called through `|| die`, so its
# critical commands cannot rely on Bash's implicit errexit behavior.
calls=()
nginx_generation="stable-container|original-start"
nginx_generation_after_up="stable-container|original-start"
nginx_generation_status=0
nginx_generation_status_after_up=0
compose_failure_pattern="up -d --no-build --pull never nginx"
if start_hfl_stack; then
	printf 'ERROR: failed Nginx Compose start was ignored\n' >&2
	exit 1
fi
[[ " ${calls[*]} " == *" compose:up -d --no-build --pull never nginx "* ]]
[[ " ${calls[*]} " != *" reload "* ]]
[[ " ${calls[*]} " != *" service-health:600 nginx "* ]]
compose_failure_pattern=""

# A continuously running gateway retains resolved upstream addresses and must
# reload after the active API/Web pool is converged.
calls=()
nginx_generation="stable-container|original-start"
nginx_generation_after_up="stable-container|original-start"
nginx_generation_status=0
nginx_generation_status_after_up=0
start_hfl_stack
[[ " ${calls[*]} " == *" reload "* ]]
[[ " ${calls[*]} " == *" service-health:600 nginx "* ]]

# A recreated gateway has already read the new configuration and follows the
# same readiness path as a clean installation.
calls=()
nginx_generation="old-container|old-start"
nginx_generation_after_up="new-container|new-start"
nginx_generation_status=0
nginx_generation_status_after_up=0
start_hfl_stack
[[ " ${calls[*]} " != *" reload "* ]]
[[ " ${calls[*]} " == *" service-health:600 nginx "* ]]

# Instance inspection failures are not equivalent to an absent or restarted
# gateway. Fail closed so a transient Docker error cannot leave stale upstreams.
calls=()
nginx_generation_status=1
nginx_generation_status_after_up=0
if start_hfl_stack; then
	printf 'ERROR: pre-start Nginx inspection failure was ignored\n' >&2
	exit 1
fi
[[ " ${calls[*]} " != *" compose:up -d --no-build --pull never nginx "* ]]

calls=()
nginx_generation="stable-container|original-start"
nginx_generation_after_up="stable-container|original-start"
nginx_generation_status=0
nginx_generation_status_after_up=1
if start_hfl_stack; then
	printf 'ERROR: post-start Nginx inspection failure was ignored\n' >&2
	exit 1
fi
[[ " ${calls[*]} " == *" compose:up -d --no-build --pull never nginx "* ]]
[[ " ${calls[*]} " != *" reload "* ]]

# The reload helper waits for a numeric, live master PID before validating and
# signaling Nginx. This protects cutover and rollback paths as well as start.
calls=()
master_attempts=0
stable_nginx_master_ready() {
	master_attempts=$((master_attempts + 1))
	((master_attempts >= 3))
}
sleep() { SECONDS=$((SECONDS + 1)); }
HFL_NGINX_RELOAD_TIMEOUT_SECONDS=5
wait_for_stable_nginx_master
[[ "${master_attempts}" -eq 3 ]]

master_attempts=0
stable_nginx_master_ready() {
	master_attempts=$((master_attempts + 1))
	return 1
}
HFL_NGINX_RELOAD_TIMEOUT_SECONDS=2
if wait_for_stable_nginx_master; then
	printf 'ERROR: unavailable Nginx master passed its readiness gate\n' >&2
	exit 1
fi
[[ "${master_attempts}" -eq 2 ]]

# Bash disables implicit errexit inside functions invoked by `if !` or `||`.
# Explicit guards must prevent a failed config test from being hidden by a
# later successful reload command.
eval "${ORIGINAL_RELOAD_STABLE_NGINX}"
calls=()
nginx_config_valid=0
nginx_reload_valid=1
wait_for_stable_nginx_master() { calls+=("master-ready"); }
compose_in_root() {
	calls+=("compose:$*")
	case "$*" in
	"exec -T nginx nginx -t") [[ "${nginx_config_valid}" -eq 1 ]] ;;
	"exec -T nginx nginx -s reload") [[ "${nginx_reload_valid}" -eq 1 ]] ;;
	esac
}
if reload_stable_nginx; then
	printf 'ERROR: failed Nginx config validation was masked\n' >&2
	exit 1
fi
[[ " ${calls[*]} " == *" compose:exec -T nginx nginx -t "* ]]
[[ " ${calls[*]} " != *" compose:exec -T nginx nginx -s reload "* ]]

calls=()
nginx_config_valid=1
nginx_reload_valid=0
if reload_stable_nginx; then
	printf 'ERROR: failed Nginx reload was masked\n' >&2
	exit 1
fi
[[ " ${calls[*]} " == *" compose:exec -T nginx nginx -s reload "* ]]

calls=()
nginx_reload_valid=1
reload_stable_nginx
[[ " ${calls[*]} " == *" master-ready compose:exec -T nginx nginx -t compose:exec -T nginx nginx -s reload "* ]]

printf 'Nginx startup and reload readiness checks passed.\n'
