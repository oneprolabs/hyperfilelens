#!/usr/bin/env bash
# Compile HyperFileLens Agent source only (Go cross-build).
# Does not fetch runtime resources, run tests, or assemble distribution archives.
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${AGENT_ROOT}/../.." && pwd)"
# shellcheck source=../../../tools/lib/version.sh
source "${REPO_ROOT}/tools/lib/version.sh"

DEFAULT_MATRIX="linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64"
MODE="release"
MODE_SET=0
PRINT_CONFIG=0
VERBOSE="${AGENT_VERBOSE:-0}"
LOG_FILE="${AGENT_LOG_FILE:-}"
OPT_VERSION=""
OPT_MATRIX=""
OPT_COMMIT=""
OPT_GO_PROXY=""
OPT_GO_SUMDB=""
SESSION_STARTED=0

hfl_now() {
	date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ
}

hfl_finish_sentence() {
	local msg="$*"
	msg="${msg%"${msg##*[![:space:]]}"}"
	case "${msg}" in
	*. | *.? | *!) printf '%s' "${msg}" ;;
	*) printf '%s.' "${msg}" ;;
	esac
}

_hfl_emit_raw() {
	local level=$1 tag
	shift
	if [[ "${HFL_LOG_TERMINAL_TIMESTAMPS:-1}" == "1" ]]; then
		printf '[%s] [%s] %s\n' "$(hfl_now)" "${level}" "$(hfl_finish_sentence "$@")" >&2
	else
		case "${level// /}" in
		INFO) tag='INFO ' ;;
		STEP) tag='....' ;;
		OK) tag=' OK ' ;;
		WARN) tag='WARN' ;;
		SKIP) tag='SKIP' ;;
		FAIL) tag='FAIL' ;;
		*) tag="${level}" ;;
		esac
		printf '[%s] %s\n' "${tag}" "$(hfl_finish_sentence "$@")" >&2
	fi
}

log_info() { _hfl_emit_raw "INFO " "$@"; }
log_ok() { _hfl_emit_raw " OK  " "$@"; }
log_step() { _hfl_emit_raw "STEP " "$@"; }
log_skip() { _hfl_emit_raw "SKIP " "$@"; }
log_warn() { _hfl_emit_raw "WARN " "$@"; }
log_fail() {
	local message=$1
	local code=${2:-1}
	_hfl_emit_raw "FAIL " "${message}"
	exit "${code}"
}

finish_session() {
	local rc=$?
	trap - EXIT
	if [[ "${SESSION_STARTED}" -eq 1 ]]; then
		if [[ "${rc}" -eq 0 ]]; then
			log_info "Agent build session finished successfully"
		else
			log_warn "Agent build session finished with errors (exit=${rc})"
		fi
	fi
	exit "${rc}"
}

require_value() {
	if [[ $# -lt 2 || -z "${2:-}" || "${2:0:1}" == "-" ]]; then
		printf 'ERROR: %s requires a value\n' "$1" >&2
		exit 2
	fi
}

usage() {
	cat <<'USAGE'
Usage: ./src/agent/scripts/build.sh [--release|--clean|--clean-all] [options]

Purpose:
  Cross-compile hfl-agent and hfl-enroll from local Go source. This script does
  not fetch Kopia, run tests, or assemble customer-facing archives.

Modes (default: --release):
  --release              Build the configured platform matrix
  --clean                Remove compiled Agent/Enroll binaries and BUILD_INFO.json only
  --clean-all            Remove every version directory under build/agent

Inputs:
  src/agent/cmd/, src/agent/internal/, src/agent/go.mod, src/agent/go.sum

Outputs:
  build/agent/<version>/<os>/<arch>/hfl-agent-*
  build/agent/<version>/<os>/<arch>/hfl-enroll-*
  build/agent/<version>/BUILD_INFO.json

Options:
  --version VERSION      Release version (env: RELEASE_VERSION)
  --matrix MATRIX        Space-separated os:arch list (env: AGENT_MATRIX)
  --commit COMMIT        Git commit embedded in binaries (env: AGENT_COMMIT)
  --go-proxy URL         Go module proxy (env: GOPROXY)
  --go-sumdb VALUE       Go checksum database (env: GOSUMDB)
  --log-file FILE        Append console output to FILE (env: AGENT_LOG_FILE)
  --verbose              Enable detailed configuration logging (env: AGENT_VERBOSE=1)
  --print-config         Print resolved configuration without building
  -h, --help             Show this help

Supported matrix entries:
  linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64

Precedence:
  CLI option > environment variable > exact Git tag/configured default

Exit codes:
  0 success; 1 build failure; 2 invalid input or missing tool; 130 interrupted

Examples:
  ./src/agent/scripts/build.sh --release
  ./src/agent/scripts/build.sh --release --version 1.2.0 --matrix "linux:amd64 linux:arm64"
  ./src/agent/scripts/build.sh --release --go-proxy "https://goproxy.cn,direct" --go-sumdb sum.golang.google.cn
  ./src/agent/scripts/build.sh --clean
  ./src/agent/scripts/build.sh --clean-all
USAGE
}

set_mode() {
	local requested=$1
	if [[ "${MODE_SET}" -eq 1 ]]; then
		printf 'ERROR: build modes are mutually exclusive\n' >&2
		exit 2
	fi
	MODE="${requested}"
	MODE_SET=1
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--release) set_mode release; shift ;;
	--clean) set_mode clean; shift ;;
	--clean-all) set_mode clean-all; shift ;;
	--version) require_value "$1" "${2:-}"; OPT_VERSION=$2; shift 2 ;;
	--matrix) require_value "$1" "${2:-}"; OPT_MATRIX=$2; shift 2 ;;
	--commit) require_value "$1" "${2:-}"; OPT_COMMIT=$2; shift 2 ;;
	--go-proxy) require_value "$1" "${2:-}"; OPT_GO_PROXY=$2; shift 2 ;;
	--go-sumdb) require_value "$1" "${2:-}"; OPT_GO_SUMDB=$2; shift 2 ;;
	--log-file) require_value "$1" "${2:-}"; LOG_FILE=$2; shift 2 ;;
	--verbose) VERBOSE=1; shift ;;
	--print-config) PRINT_CONFIG=1; shift ;;
	-h | --help) usage; exit 0 ;;
	-*) printf 'ERROR: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
	*) printf 'ERROR: unexpected argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
	esac
done

case "${VERBOSE}" in
0 | 1 | true | false | yes | no) ;;
*) printf 'ERROR: AGENT_VERBOSE must be 0 or 1\n' >&2; exit 2 ;;
esac
case "${VERBOSE}" in true | yes) VERBOSE=1 ;; false | no) VERBOSE=0 ;; esac

AGENT_VERSION="$(normalize_artifact_id "${OPT_VERSION:-$(resolve_release_version)}")" || exit $?
MATRIX="${OPT_MATRIX:-${AGENT_MATRIX:-${DEFAULT_MATRIX}}}"
COMMIT="${OPT_COMMIT:-${AGENT_COMMIT:-$(resolve_commit_full "${REPO_ROOT}")}}"
AGENT_ARTIFACTS_DIR="${REPO_ROOT}/build/agent"
WORK_ROOT="${AGENT_ARTIFACTS_DIR}/${AGENT_VERSION}"

[[ -n "${OPT_GO_PROXY}" ]] && export GOPROXY="${OPT_GO_PROXY}"
[[ -n "${OPT_GO_SUMDB}" ]] && export GOSUMDB="${OPT_GO_SUMDB}"

validate_matrix() {
	local item seen=" "
	[[ -n "${MATRIX//[[:space:]]/}" ]] || log_fail "Matrix cannot be empty" 2
	for item in ${MATRIX}; do
		case "${item}" in
		linux:amd64 | linux:arm64 | darwin:amd64 | darwin:arm64 | windows:amd64) ;;
		*) log_fail "Unsupported matrix entry ${item}" 2 ;;
		esac
		if [[ "${seen}" == *" ${item} "* ]]; then
			log_fail "Duplicate matrix entry ${item}" 2
		fi
		seen+="${item} "
	done
}

print_config() {
	cat <<CONFIG
mode=${MODE}
version=${AGENT_VERSION}
commit=${COMMIT}
matrix=${MATRIX}
go_proxy=${GOPROXY:-<go-default>}
go_sumdb=${GOSUMDB:-<go-default>}
output=${WORK_ROOT}
CONFIG
}

setup_log_file() {
	[[ "${HFL_PARENT_SESSION:-0}" != "1" ]] || return 0
	[[ "${HFL_LOG_TEE_ACTIVE:-0}" != "1" ]] || return 0
	[[ -n "${LOG_FILE}" ]] || return 0
	mkdir -p "$(dirname "${LOG_FILE}")"
	exec > >(tee -a "${LOG_FILE}") 2>&1
}

clean_compiled_outputs() {
	if [[ ! -d "${AGENT_ARTIFACTS_DIR}" ]]; then
		log_skip "Agent build directory is not present (${AGENT_ARTIFACTS_DIR})"
		return 0
	fi
	log_step "Removing compiled Agent and Enroll outputs"
	find "${AGENT_ARTIFACTS_DIR}" -mindepth 4 -maxdepth 4 -type f \
		\( -name 'hfl-agent-*' -o -name 'hfl-enroll-*' \) -delete
	find "${AGENT_ARTIFACTS_DIR}" -mindepth 2 -maxdepth 2 -type f -name BUILD_INFO.json -delete
	log_ok "Removed compiled outputs while preserving dependencies and packages"
}

clean_all_outputs() {
	[[ "${AGENT_ARTIFACTS_DIR}" == "${REPO_ROOT}/build/agent" ]] \
		|| log_fail "Refusing to clean unexpected path ${AGENT_ARTIFACTS_DIR}" 2
	if [[ ! -d "${AGENT_ARTIFACTS_DIR}" ]]; then
		log_skip "Agent build directory is not present (${AGENT_ARTIFACTS_DIR})"
		return 0
	fi
	log_step "Removing all Agent build versions under ${AGENT_ARTIFACTS_DIR}"
	find "${AGENT_ARTIFACTS_DIR}" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
	log_ok "Removed all Agent build versions"
}

write_build_info() {
	command -v python3 >/dev/null 2>&1 || log_fail "python3 is required to write BUILD_INFO.json" 2
	VERSION="${AGENT_VERSION}" COMMIT_VALUE="${COMMIT}" MATRIX_VALUE="${MATRIX}" \
		python3 - "${WORK_ROOT}/BUILD_INFO.json" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "version": os.environ["VERSION"],
    "commit": os.environ["COMMIT_VALUE"],
    "matrix": os.environ["MATRIX_VALUE"].split(),
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
	log_ok "Wrote ${WORK_ROOT}/BUILD_INFO.json"
}

build_release() {
	command -v go >/dev/null 2>&1 || log_fail "Go is required to build Agent binaries" 2
	command -v python3 >/dev/null 2>&1 || log_fail "python3 is required to write build metadata" 2
	cd "${AGENT_ROOT}"
	mkdir -p "${WORK_ROOT}"
	local ldflags="-s -w -X hyperfilelens/agent/internal/selfupdate.Version=${AGENT_VERSION} -X hyperfilelens/agent/internal/selfupdate.Commit=${COMMIT}"
	local item goos goarch name enroll_name dir

	for item in ${MATRIX}; do
		IFS=: read -r goos goarch <<<"${item}"
		dir="${WORK_ROOT}/${goos}/${goarch}"
		mkdir -p "${dir}"
		name="hfl-agent-${goos}-${goarch}"
		[[ "${goos}" == windows ]] && name+=".exe"
		log_step "Building hfl-agent for ${goos}/${goarch}"
		if ! GOOS="${goos}" GOARCH="${goarch}" CGO_ENABLED=0 \
			go build -buildvcs=false -trimpath -ldflags "${ldflags}" -o "${dir}/${name}" ./cmd/agent; then
			log_fail "Failed to build hfl-agent for ${goos}/${goarch}"
		fi
		log_ok "Built ${dir}/${name}"

		enroll_name="hfl-enroll-${goos}-${goarch}"
		[[ "${goos}" == windows ]] && enroll_name+=".exe"
		log_step "Building hfl-enroll for ${goos}/${goarch}"
		if ! GOOS="${goos}" GOARCH="${goarch}" CGO_ENABLED=0 \
			go build -buildvcs=false -trimpath -ldflags "${ldflags}" -o "${dir}/${enroll_name}" ./cmd/enroll; then
			log_fail "Failed to build hfl-enroll for ${goos}/${goarch}"
		fi
		log_ok "Built ${dir}/${enroll_name}"
	done
	write_build_info
}

validate_matrix
if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
	print_config
	exit 0
fi

setup_log_file
trap 'exit 130' INT TERM
if [[ "${HFL_PARENT_SESSION:-0}" != "1" ]]; then
	trap finish_session EXIT
	SESSION_STARTED=1
	log_info "Agent build session started"
fi
log_info "Mode: ${MODE}"
log_info "Version: ${AGENT_VERSION}"
log_info "Commit: ${COMMIT}"
log_info "Matrix: ${MATRIX}"
if [[ "${VERBOSE}" -eq 1 ]]; then
	log_info "Go proxy: ${GOPROXY:-Go default}"
	log_info "Go checksum database: ${GOSUMDB:-Go default}"
	log_info "Output: ${WORK_ROOT}"
fi

case "${MODE}" in
release) build_release ;;
clean) clean_compiled_outputs ;;
clean-all) clean_all_outputs ;;
esac
