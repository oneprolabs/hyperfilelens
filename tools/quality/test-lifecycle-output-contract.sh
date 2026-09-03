#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../deploy/installer/install.sh
source "${ROOT_REPO}/deploy/installer/install.sh"

uninstall_help="$(usage)"
grep -F 'Usage: ./install.sh [command] [options]' <<<"${uninstall_help}" >/dev/null
grep -F 'platform-gateway    Manage the installer-owned Platform Data Gateway' <<<"${uninstall_help}" >/dev/null
grep -F 'uninstall           Completely remove the installer-managed deployment' <<<"${uninstall_help}" >/dev/null
grep -F -- '--keep-data                 Remove all managed runtime components while retaining' <<<"${uninstall_help}" >/dev/null
grep -F -- '--purge-all                 Compatibility alias for the default complete removal' <<<"${uninstall_help}" >/dev/null
grep -F 'Selective compatibility options:' <<<"${uninstall_help}" >/dev/null
grep -F 'To retain an uninstall log, set --log-file to a path outside' <<<"${uninstall_help}" >/dev/null
grep -F 'sudo ./install.sh uninstall --keep-data' <<<"${uninstall_help}" >/dev/null

fixture="$(mktemp -d)"
trap 'rm -rf "${fixture}"' EXIT
ROOT="${fixture}"
LOG_FILE="${fixture}/install.log"
INTERACTIVE_SESSION=1
mkdir -p "${ROOT}/data/sourcelens/config" "${ROOT}/sourcelens"
printf '%s\n' \
	'SEED_INITIAL_DATA=1' \
	'SEED_ADMIN_EMAIL=admin@hyperfilelens.com' \
	'SEED_ADMIN_PASSWORD=Admin@123' \
	'SEED_ORG_NAME=HyperFileLens' \
	'HFL_WEBSITE_PORT=11442' \
	'HFL_TENANT_PORT=11443' \
	'HFL_ADMIN_PORT=11444' \
	'SOURCELENS_MODE=bundled' \
	'SOURCELENS_CONSOLE_PORT=11445' >"${ROOT}/.env"
printf '%s\n' \
	'DJANGO_SUPERUSER_USERNAME=admin' \
	'DJANGO_SUPERUSER_EMAIL=admin@example.com' \
	'DJANGO_SUPERUSER_PASSWORD=adminpassword' >"${ROOT}/data/sourcelens/config/.env"

read_version() { printf '0.2.0'; }
read_edition_from_dir() { printf 'Enterprise'; }
resolve_console_host() { printf '192.0.2.10'; }
package_has_sourcelens() { return 0; }
sourcelens_installed() { return 0; }

output="$({
	print_banner 'HyperFileLens Installer'
	print_result 'Installation completed successfully'
	print_console_access_summary
} 2>&1)"

[[ "$(grep -c 'INSTALLER' <<<"${output}")" -eq 1 ]]
grep -F 'https://192.0.2.10:11442/en/' <<<"${output}" >/dev/null
grep -F 'https://192.0.2.10:11443/' <<<"${output}" >/dev/null
grep -F 'https://192.0.2.10:11444/admin/' <<<"${output}" >/dev/null
grep -F 'https://192.0.2.10:11445/' <<<"${output}" >/dev/null
grep -F 'admin@hyperfilelens.com' <<<"${output}" >/dev/null
grep -F 'Admin@123' <<<"${output}" >/dev/null
grep -F 'adminpassword' <<<"${output}" >/dev/null
grep -F 'install.sh upgrade --from /path/to/new-release.tar.gz' <<<"${output}" >/dev/null
grep -F 'install.sh uninstall' <<<"${output}" >/dev/null

# A bundled package is not the same as a running Insight installation. The
# --hfl-only summary must not advertise an unavailable console or credentials.
sourcelens_installed() { return 1; }
hfl_only_output="$(print_console_access_summary 2>&1)"
if grep -F 'Insight Console' <<<"${hfl_only_output}" >/dev/null; then
	echo 'HFL-only summary advertised an unavailable Insight Console' >&2
	exit 1
fi
sourcelens_installed() { return 0; }

grep -F 'hfl_print_banner "${title}"' "${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F 'HFL_PARENT_SESSION=1 "${ROOT}/dev/sourcelens.sh"' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F 'gateway-install --yes --no-banner' \
	"${ROOT_REPO}/deploy/installer/install.sh" >/dev/null

capture_log="${fixture}/dev-session.log"
capture_stdout="${fixture}/dev-session.stdout"
capture_stderr="${fixture}/dev-session.stderr"
HFL_LOG_CAPTURE_STDOUT=1 \
HFL_LOG_TERMINAL_TIMESTAMPS=0 \
HFL_LOG_SESSION_MESSAGES=0 \
HFL_CAPTURE_TEST_LOG="${capture_log}" \
	bash -c '
set -euo pipefail
source "$1/tools/lib/logging.sh"
hfl_logging_configure test "$HFL_CAPTURE_TEST_LOG" 0
hfl_log_info "structured detail"
hfl_log_step "structured step"
hfl_log_ok "structured success"
printf "%s\n" "raw child stdout"
printf "progress-one\rprogress-two\n"
' _ "${ROOT_REPO}" >"${capture_stdout}" 2>"${capture_stderr}"
for _ in {1..50}; do
	if grep -F 'raw child stdout' "${capture_log}" >/dev/null 2>&1 \
		&& grep -F 'raw child stdout' "${capture_stdout}" >/dev/null 2>&1 \
		&& grep -F '[INFO ] structured detail' "${capture_stderr}" >/dev/null 2>&1; then
		break
	fi
	sleep 0.02
done
[[ "$(stat -c '%a' "${capture_log}")" == "600" ]]
grep -Fx 'raw child stdout' "${capture_stdout}" >/dev/null
grep -Fx '[INFO ] structured detail' "${capture_stderr}" >/dev/null
grep -Fx '[....] structured step' "${capture_stderr}" >/dev/null
grep -Fx '[ OK ] structured success' "${capture_stderr}" >/dev/null
if grep -F 'structured detail' "${capture_stdout}" >/dev/null; then
	echo 'structured stderr was redirected to stdout' >&2
	exit 1
fi
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] \[INFO *\] structured detail$' \
	"${capture_log}" >/dev/null
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] raw child stdout$' \
	"${capture_log}" >/dev/null
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] progress-one$' \
	"${capture_log}" >/dev/null
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] progress-two$' \
	"${capture_log}" >/dev/null

# Timestamped output follows the Agent installer contract: status is the
# fixed-width field immediately after the timestamp, followed by a compact
# component name. ANSI styling is disabled in persisted/captured output.
timestamp_output="$({
	source "${ROOT_REPO}/tools/lib/logging.sh"
	export HFL_LOG_TERMINAL_TIMESTAMPS=1 HFL_LOG_COMPONENT=sourcelens-dev HFL_LOG_COLOR=0
	hfl_log_step "Preparing SourceLens"
	hfl_log_emit_with_component 'OUT ' docker 'Container sourcelens-api Started'
	hfl_log_ok "Insight services are prepared"
} 2>&1)"
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] \[\.\.\.\.\] \[sourcelens\] Preparing SourceLens$' <<<"${timestamp_output}" >/dev/null
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] \[OUT \] \[docker\] Container sourcelens-api Started$' <<<"${timestamp_output}" >/dev/null
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] \[ OK \] \[sourcelens\] Insight services are prepared$' <<<"${timestamp_output}" >/dev/null

# Native Docker/Compose builds must keep their interactive renderer when the
# dev session has an original terminal. npm runs inside those BuildKit stages;
# it must remain native output rather than being wrapped line-by-line.
grep -F 'hfl_run_native_command env' "${ROOT_REPO}/tools/sourcelens/common.sh" >/dev/null
grep -F 'BUILDKIT_PROGRESS="${BUILDKIT_PROGRESS:-auto}"' \
	"${ROOT_REPO}/tools/sourcelens/common.sh" >/dev/null
grep -F 'hfl_run_native_command env' "${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F 'hfl_run_native_command "${ROOT}/website/build.sh"' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F 'hfl_run_native_command docker build' "${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F 'npm config set audit false' "${ROOT_REPO}/tools/sourcelens/common.sh" >/dev/null
grep -F 'npm config set fetch-retries 5' "${ROOT_REPO}/tools/sourcelens/common.sh" >/dev/null

# Long structured messages wrap only in the terminal copy. The persisted
# record remains one complete line, with no truncation or repeated prefix.
wrapped_terminal="${fixture}/wrapped-terminal.log"
wrapped_session="${fixture}/wrapped-session.log"
wrapped_message='[2026-08-23T02:43:53.000Z] [INFO] [sourcelens] SourceLens app images already built; compose build skipped; source_stamp=v3:0.47.9:3634354953c8119755128a47cd2db39259086807:6e8dbf651a5a7ea7709fe7c6f810978b5aa051de441a1a1da4f44e81346dda47'
HFL_LOG_TERMINAL_WRAP_COLUMNS=70 HFL_WRAPPED_MESSAGE="${wrapped_message}" \
	bash -c '
set -euo pipefail
source "$1/tools/lib/logging.sh"
exec 5>"$2"
printf "%s\n" "$HFL_WRAPPED_MESSAGE" | hfl_log_capture_stream "$3" 5
exec 5>&-
' _ "${ROOT_REPO}" "${wrapped_terminal}" "${wrapped_session}"
grep -Fx "${wrapped_message}" "${wrapped_session}" >/dev/null
grep -F 'source_stamp=v3:0.47.9:' "${wrapped_terminal}" >/dev/null
grep -E '^ +source_stamp=' "${wrapped_terminal}" >/dev/null
[[ "$(wc -l <"${wrapped_terminal}")" -gt 1 ]]

git_output="$({
	source "${ROOT_REPO}/tools/sourcelens/common.sh"
	export HFL_LOG_TERMINAL_TIMESTAMPS=1 HFL_LOG_COLOR=0
	sourcelens_git_output_command printf '%s\n' 'Synchronizing submodule url for test'
} 2>&1)"
grep -E '^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T.*\] \[OUT \] \[git\] Synchronizing submodule url for test$' \
	<<<"${git_output}" >/dev/null

git_block_without_timestamp="$({
	source "${ROOT_REPO}/tools/sourcelens/common.sh"
	export HFL_LOG_TERMINAL_TIMESTAMPS=0 HFL_LOG_COLOR=0
	printf 'From https://example.test\n * [new branch]      feat/x -> origin/feat/x\n   abc..def          main -> origin/main\n' \
		| hfl_log_output_block git
} 2>&1)"
grep -Fx '[OUT ] From https://example.test' <<<"${git_block_without_timestamp}" >/dev/null
grep -E '^ +\* \[new branch\]' <<<"${git_block_without_timestamp}" >/dev/null
if grep -F '[git]' <<<"${git_block_without_timestamp}" >/dev/null; then
	echo 'timestamp-free native output must not add a component field' >&2
	exit 1
fi

# A terminal that declares itself as dumb must not receive ANSI styling in
# auto mode. Explicit HFL_LOG_COLOR=1/always remains an opt-in override.
dumb_color_result="$({
	export TERM=dumb HFL_LOG_COLOR=auto
	source "${ROOT_REPO}/tools/lib/logging.sh"
	if hfl_log_color_enabled; then
		printf 'enabled'
	else
		printf 'disabled'
	fi
} 2>/dev/null)"
[[ "${dumb_color_result}" == "disabled" ]]

failure_log="${fixture}/dev-failure.log"
failure_stderr="${fixture}/dev-failure.stderr"
set +e
HFL_LOG_CAPTURE_STDOUT=1 \
HFL_LOG_TERMINAL_TIMESTAMPS=0 \
HFL_LOG_SESSION_MESSAGES=0 \
HFL_FAILURE_LOG="${failure_log}" \
	bash -c '
set -euo pipefail
source "$1/tools/lib/logging.sh"
hfl_logging_configure test "$HFL_FAILURE_LOG"
hfl_logging_start
trap '\''rc=$?; hfl_logging_finish "$rc"'\'' EXIT
exit 7
' _ "${ROOT_REPO}" >/dev/null 2>"${failure_stderr}"
failure_rc=$?
set -e
[[ "${failure_rc}" -eq 7 ]]
for _ in {1..50}; do
	grep -F 'Session exited with status 7; full log:' "${failure_log}" >/dev/null 2>&1 && break
	sleep 0.02
done
grep -F '[FAIL] Session exited with status 7; full log:' "${failure_stderr}" >/dev/null
grep -F 'Session exited with status 7; full log:' "${failure_log}" >/dev/null
[[ "$(grep -Fc 'Session exited with status 7; full log:' "${failure_stderr}")" -eq 1 ]]
[[ "$(grep -Fc 'Session exited with status 7; full log:' "${failure_log}")" -eq 1 ]]

for script in build.sh fetch-deps.sh package.sh; do
	grep -F 'HFL_PARENT_SESSION' "${ROOT_REPO}/src/agent/scripts/${script}" >/dev/null
	grep -F 'HFL_LOG_TERMINAL_TIMESTAMPS' "${ROOT_REPO}/src/agent/scripts/${script}" >/dev/null
	grep -F '[[ "${HFL_PARENT_SESSION:-0}" != "1" ]] || return 0' \
		"${ROOT_REPO}/src/agent/scripts/${script}" >/dev/null
	grep -F '[[ "${HFL_LOG_TEE_ACTIVE:-0}" != "1" ]] || return 0' \
		"${ROOT_REPO}/src/agent/scripts/${script}" >/dev/null
done
[[ "$(grep -Fc 'HFL_PARENT_SESSION=1 "${AGENT_DIR}/scripts/' "${ROOT_REPO}/tools/agent/publish.sh")" -eq 4 ]]

# Unknown commands cannot escape the managed log directory through their name.
safe_log_root="${fixture}/safe-log-root"
safe_log_result="${fixture}/safe-log-result"
HFL_SAFE_LOG_ROOT="${safe_log_root}" HFL_SAFE_LOG_RESULT="${safe_log_result}" \
	bash -c '
set -euo pipefail
source "$1/deploy/installer/install.sh"
INSTALL_DIR="$HFL_SAFE_LOG_ROOT"
LOG_FILE=""
configure_logging "../../escape"
printf "%s\n" "$LOG_FILE" >"$HFL_SAFE_LOG_RESULT"
' _ "${ROOT_REPO}" >/dev/null 2>&1
safe_log_file="$(cat "${safe_log_result}")"
[[ "${safe_log_file}" == "${safe_log_root}"/logs/operation-*.log ]]
[[ ! -L "${safe_log_file}" && "$(stat -c '%a' "${safe_log_file}")" == "600" ]]

# A configured symlink is rejected; fallback uses a fresh, private file.
symlink_target="${fixture}/symlink-target.log"
symlink_log="${fixture}/symlink.log"
symlink_result="${fixture}/symlink-result"
: >"${symlink_target}"
ln -s "${symlink_target}" "${symlink_log}"
HFL_SYMLINK_LOG="${symlink_log}" HFL_SYMLINK_RESULT="${symlink_result}" \
	bash -c '
set -euo pipefail
source "$1/tools/lib/logging.sh"
hfl_logging_configure test "$HFL_SYMLINK_LOG"
printf "%s\n" "$HFL_LOG_FILE" >"$HFL_SYMLINK_RESULT"
' _ "${ROOT_REPO}" >/dev/null 2>&1
fallback_log="$(cat "${symlink_result}")"
[[ "${fallback_log}" != "${symlink_log}" && ! -L "${fallback_log}" ]]
[[ "$(stat -c '%a' "${fallback_log}")" == "600" && ! -s "${symlink_target}" ]]
rm -f "${fallback_log}"

# Explicit Insight-data purge also removes orphaned data when no runtime is
# currently installed.
orphan_root="${fixture}/orphaned-insight"
mkdir -p "${orphan_root}/data/sourcelens"
ROOT="${orphan_root}"
sourcelens_installed() { return 1; }
sourcelens_runtime_present() { return 1; }
uninstall_bundled_sourcelens 1 >/dev/null 2>&1
[[ ! -e "${orphan_root}/data/sourcelens" ]]

# Disabled HFL seeding must not advertise credentials for an account that does
# not exist in the development environment.
dev_fixture="${fixture}/dev"
mkdir -p "${dev_fixture}"
printf '%s\n' \
	'SEED_INITIAL_DATA=0' \
	'SOURCELENS_MODE=bundled' >"${dev_fixture}/.env"
dev_output="$({
	source "${ROOT_REPO}/dev/stack.sh"
	ROOT="${dev_fixture}"
	WITH_SOURCELENS=0
	LOG_FILE="${dev_fixture}/dev.log"
	print_urls
} 2>&1)"
grep -F 'Not configured (SEED_INITIAL_DATA=0)' <<<"${dev_output}" >/dev/null
if grep -F 'Password' <<<"${dev_output}" >/dev/null; then
	echo 'Dev summary advertised credentials while HFL seeding was disabled' >&2
	exit 1
fi

# The development target summary identifies the command, source revisions,
# SourceLens mode, and both host/runtime platforms before lifecycle output.
target_output="$({
	source "${ROOT_REPO}/dev/stack.sh"
	ROOT="${fixture}"
	CMD=restart
	restart_force=1
	WITH_SOURCELENS=1
	SOURCELENS_GIT_REF=v0.47.9
	EXTENSION_SOURCES=("https://github.com/example/hyperfilelens-ee.git@v1.2.3")
	LOG_FILE="${fixture}/build/logs/dev-restart.log"
	print_dev_target
} 2>&1)"
grep -F '  Command        restart --force' <<<"${target_output}" >/dev/null
grep -F '  Extension      remote Git source configured' <<<"${target_output}" >/dev/null
grep -F '  Extension rev  v1.2.3' <<<"${target_output}" >/dev/null
grep -F '  SourceLens     bundled / v0.47.9' <<<"${target_output}" >/dev/null
grep -F '  Host platform  ' <<<"${target_output}" >/dev/null
grep -F '  Runtime        linux/amd64' <<<"${target_output}" >/dev/null
grep -F '  Session log    build/logs/dev-restart.log' <<<"${target_output}" >/dev/null

printf 'Lifecycle output contract checks passed.\n'
