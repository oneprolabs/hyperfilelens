#!/usr/bin/env bash
# Validate standalone Agent lifecycle output and timestamped file logging.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT}/src/agent/packaging/install/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

run_success_case() (
	# Load helpers without dispatching the real installer.
	# shellcheck disable=SC1090
	source <(sed '/^bundle_agent()/,$d' "${installer}")
	begin_install_log "${tmp}/success"
	hfl_print_banner "Source Host" "Installer"
	hfl_print_section "Target"
	hfl_print_value "Role" "Source Host"
	hfl_print_section "Installing Agent"
	log_ok "Agent files were installed."
	hfl_print_result "Installation completed successfully"
	finish_install_log 0
)

run_success_case >"${tmp}/success.out" 2>&1
success_log="${tmp}/success/logs/install.log"
grep -F 'HyperFileLens Source Host Installer' "${tmp}/success.out" >/dev/null
grep -F 'Installation completed successfully' "${tmp}/success.out" >/dev/null
grep -F 'HyperFileLens Source Host Installer' "${success_log}" >/dev/null
grep -F 'Installation completed successfully' "${success_log}" >/dev/null
if awk 'NF && $0 !~ /^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\] / { exit 1 }' \
	"${success_log}"; then
	:
else
	printf 'Agent install log contains a non-timestamped line\n' >&2
	exit 1
fi

set +e
(
	# shellcheck disable=SC1090
	source <(sed '/^bundle_agent()/,$d' "${installer}")
	begin_install_log "${tmp}/failure" "upgrade"
	log_fail "Simulated upgrade failure" 7
) >"${tmp}/failure.out" 2>&1
status=$?
set -e
[[ "${status}" -eq 7 ]]
failure_log="${tmp}/failure/logs/install.log"
grep -F '[FAIL] Simulated upgrade failure.' "${tmp}/failure.out" >/dev/null
if grep -F 'Simulated upgrade failure 7.' "${tmp}/failure.out" >/dev/null; then
	printf 'Agent failure output leaked the numeric exit code into the message\n' >&2
	exit 1
fi
grep -F 'Install session finished with errors (exit=7).' "${failure_log}" >/dev/null

set +e
(
	# An unexpected shell failure must still produce one useful final failure and
	# close the timestamped session log.
	# shellcheck disable=SC1090
	source <(sed '/^bundle_agent()/,$d' "${installer}")
	begin_install_log "${tmp}/unexpected" "upgrade"
	false
) >"${tmp}/unexpected.out" 2>&1
status=$?
set -e
[[ "${status}" -eq 1 ]]
[[ "$(grep -cF '[FAIL]' "${tmp}/unexpected.out")" -eq 1 ]]
grep -F 'Upgrade failed (exit code 1)' "${tmp}/unexpected.out" >/dev/null
grep -F 'Install session finished with errors (exit=1).' \
	"${tmp}/unexpected/logs/install.log" >/dev/null

set +e
(
	# A helper may fail inside command substitution; the outer EXIT trap must not
	# add a second generic failure after the specific message was already logged.
	# shellcheck disable=SC1090
	source <(sed '/^bundle_agent()/,$d' "${installer}")
	begin_install_log "${tmp}/subshell" "upgrade"
	value="$(log_fail "Specific package source failure" 2)"
	printf '%s\n' "${value}"
) >"${tmp}/subshell.out" 2>&1
status=$?
set -e
[[ "${status}" -eq 2 ]]
[[ "$(grep -cF '[FAIL]' "${tmp}/subshell.out")" -eq 1 ]]
grep -F '[FAIL] Specific package source failure.' "${tmp}/subshell.out" >/dev/null

printf 'Agent installer output checks passed.\n'
