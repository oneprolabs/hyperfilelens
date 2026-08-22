#!/usr/bin/env bash
# Prepare Agent runtime resources (Kopia CLI and Ubuntu NAS debs).
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${AGENT_ROOT}/../.." && pwd)"
# shellcheck source=../../../tools/lib/version.sh
source "${REPO_ROOT}/tools/lib/version.sh"
# shellcheck source=../../../tools/kopia/common.sh
source "${REPO_ROOT}/tools/kopia/common.sh"
# shellcheck source=../../../tools/lib/logging.sh
source "${REPO_ROOT}/tools/lib/logging.sh"
kopia_load_config

DEFAULT_MATRIX="linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64"
VERBOSE="${AGENT_VERBOSE:-0}"
LOG_FILE="${AGENT_LOG_FILE:-}"
PRINT_CONFIG=0
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
		HFL_LOG_COMPONENT=agent \
			hfl_log_emit "${level// /}" "$(hfl_finish_sentence "$@")"
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
			log_info "Agent dependency fetch session finished successfully"
		else
			log_warn "Agent dependency fetch session finished with errors (exit=${rc})"
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
Usage: ./src/agent/scripts/fetch-deps.sh [--all|--kopia|--nas-deps] [options]

Purpose:
  Prepare external Agent runtime resources. This script does not compile HFL
  Agent source or assemble customer-facing archives. With no component, --all is used.

Components:
  --all                  Prepare Kopia CLI binaries and Ubuntu NAS debs
  --kopia                Prepare the canonical Kopia binary matrix only
  --nas-deps             Fetch nfs-common/cifs-utils offline debs through Docker

Inputs:
  tools/kopia/defaults.env, Kopia Git/Release, ubuntu:24.04, Ubuntu apt repositories

Outputs:
  build/kopia/dist/<os>/<arch>/kopia[.exe]
  build/kopia/KOPIA_INFO.json
  build/dependencies/agent/ubuntu-{20.04,22.04,24.04}/<arch>/*.deb
  build/dependencies/agent/ubuntu-{20.04,22.04,24.04}/<arch>/MANIFEST.json

Options:
  --version VERSION                Release version (env: RELEASE_VERSION)
  --matrix MATRIX                  Space-separated os:arch list (env: AGENT_MATRIX)
  --force                          Refresh cached inputs (env: AGENT_FORCE_FETCH=1)
  --pull                           Refresh the NAS Ubuntu image even when a matching local image exists
  --kopia-mode MODE                build or download
  --kopia-git-url URL              Kopia source repository URL
  --kopia-ref REF                  Kopia release ref in vX.Y.Z form
  --github-download-mirror URL     GitHub Git/release mirror (env: GITHUB_DOWNLOAD_MIRROR)
  --github-token TOKEN             GitHub API token (env: GITHUB_TOKEN; environment recommended)
  --ubuntu2404-arch ARCH           amd64 | arm64 | all (env: AGENT_UBUNTU2404_ARCH)
  --docker-download-mirror URL     Docker Hub mirror (env: DOCKER_DOWNLOAD_MIRROR)
  --docker-pull-timeout SECONDS    Timeout for each Docker pull attempt (env: DOCKER_PULL_TIMEOUT_SECONDS)
  --apt-mirror URL                 Ubuntu apt mirror (env: APT_MIRROR)
  --log-file FILE                  Append console output to FILE (env: AGENT_LOG_FILE)
  --verbose                        Enable detailed source logging (env: AGENT_VERBOSE=1)
  --print-config                   Print resolved configuration without fetching
  -h, --help                       Show this help

Supported matrix entries:
  linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64

Precedence:
  CLI option > environment variable > tools/kopia/defaults.env

Exit codes:
  0 success; 1 fetch failure; 2 invalid input or missing tool; 130 interrupted

Examples:
  ./src/agent/scripts/fetch-deps.sh --all
  ./src/agent/scripts/fetch-deps.sh --kopia --matrix "linux:amd64 linux:arm64"
  ./src/agent/scripts/fetch-deps.sh --nas-deps --ubuntu2404-arch amd64

  # Optional third-party accelerators for networks with restricted upstream access.
  ./src/agent/scripts/fetch-deps.sh --all \
    --github-download-mirror https://ghfast.top \
    --docker-download-mirror docker.m.daocloud.io \
    --apt-mirror https://mirrors.tuna.tsinghua.edu.cn

Mirror examples are not operated by HyperFileLens. Download-mode artifacts are
verified against checksums.txt published with the selected upstream release.
USAGE
}

DO_KOPIA=0
DO_NAS=0
FORCE=0
FORCE_PULL=0
EXPLICIT=0
OPT_VERSION=""
OPT_MATRIX=""
OPT_UBUNTU2404_ARCH=""
OPT_GITHUB_DOWNLOAD_MIRROR=""
OPT_GITHUB_TOKEN=""
OPT_DOCKER_DOWNLOAD_MIRROR=""
OPT_DOCKER_PULL_TIMEOUT=""
OPT_APT_MIRROR=""

while [[ $# -gt 0 ]]; do
	case "$1" in
	--kopia)
		DO_KOPIA=1
		EXPLICIT=1
		shift
		;;
	--nas-deps)
		DO_NAS=1
		EXPLICIT=1
		shift
		;;
	--all)
		DO_KOPIA=1
		DO_NAS=1
		EXPLICIT=1
		shift
		;;
	--force)
		FORCE=1
		shift
		;;
	--pull)
		FORCE_PULL=1
		shift
		;;
	--log-file)
		require_value "$1" "${2:-}"
		LOG_FILE="$2"
		shift 2
		;;
	--verbose)
		VERBOSE=1
		shift
		;;
	--print-config)
		PRINT_CONFIG=1
		shift
		;;
	--version)
		require_value "$1" "${2:-}"
		OPT_VERSION="$2"
		shift 2
		;;
	--matrix)
		require_value "$1" "${2:-}"
		OPT_MATRIX="$2"
		shift 2
		;;
	--kopia-mode)
		require_value "$1" "${2:-}"
		KOPIA_ARTIFACT_MODE="$2"
		shift 2
		;;
	--kopia-git-url)
		require_value "$1" "${2:-}"
		KOPIA_GIT_URL="$2"
		shift 2
		;;
	--kopia-ref)
		require_value "$1" "${2:-}"
		KOPIA_GIT_REF="$2"
		shift 2
		;;
	--ubuntu2404-arch)
		require_value "$1" "${2:-}"
		OPT_UBUNTU2404_ARCH="$2"
		shift 2
		;;
	--github-download-mirror)
		require_value "$1" "${2:-}"
		OPT_GITHUB_DOWNLOAD_MIRROR="$2"
		shift 2
		;;
	--github-token)
		require_value "$1" "${2:-}"
		OPT_GITHUB_TOKEN="$2"
		shift 2
		;;
	--docker-download-mirror)
		require_value "$1" "${2:-}"
		OPT_DOCKER_DOWNLOAD_MIRROR="$2"
		shift 2
		;;
	--docker-pull-timeout)
		require_value "$1" "${2:-}"
		OPT_DOCKER_PULL_TIMEOUT="$2"
		shift 2
		;;
	--apt-mirror)
		require_value "$1" "${2:-}"
		OPT_APT_MIRROR="$2"
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	-*)
		printf 'ERROR: unknown option: %s\n' "$1" >&2
		usage >&2
		exit 2
		;;
	*)
		printf 'ERROR: unexpected argument: %s\n' "$1" >&2
		usage >&2
		exit 2
		;;
	esac
done

case "${VERBOSE}" in
0 | 1 | true | false | yes | no) ;;
*) printf 'ERROR: AGENT_VERBOSE must be 0 or 1\n' >&2; exit 2 ;;
esac
case "${VERBOSE}" in true | yes) VERBOSE=1 ;; false | no) VERBOSE=0 ;; esac

case "${AGENT_FORCE_FETCH:-0}" in
1 | true | yes) FORCE=1 ;;
0 | false | no | "") ;;
*) printf 'ERROR: AGENT_FORCE_FETCH must be 0 or 1\n' >&2; exit 2 ;;
esac
case "${AGENT_FORCE_PULL:-0}" in
1 | true | yes) FORCE_PULL=1 ;;
0 | false | no | "") ;;
*) printf 'ERROR: AGENT_FORCE_PULL must be 0 or 1\n' >&2; exit 2 ;;
esac

AGENT_VERSION="$(normalize_artifact_id "${OPT_VERSION:-$(resolve_release_version)}")" || exit $?
case "${KOPIA_ARTIFACT_MODE}" in build | download) ;; *) printf 'ERROR: invalid Kopia mode: %s\n' "${KOPIA_ARTIFACT_MODE}" >&2; exit 2 ;; esac
[[ "${KOPIA_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] \
	|| { printf 'ERROR: invalid Kopia ref: %s\n' "${KOPIA_GIT_REF}" >&2; exit 2; }
KOPIA_VERSION="${BASH_REMATCH[1]}"
MATRIX="${OPT_MATRIX:-${AGENT_MATRIX:-${DEFAULT_MATRIX}}}"
AGENT_ARTIFACTS_DIR="${REPO_ROOT}/build/agent"
WORK_ROOT="${AGENT_ARTIFACTS_DIR}/${AGENT_VERSION}"
NAS_DEPS_BASE="${REPO_ROOT}/build/dependencies/agent"
GITHUB_DOWNLOAD_MIRROR="${OPT_GITHUB_DOWNLOAD_MIRROR:-${GITHUB_DOWNLOAD_MIRROR:-}}"
GITHUB_TOKEN="${OPT_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
DOCKER_DOWNLOAD_MIRROR="${OPT_DOCKER_DOWNLOAD_MIRROR:-${DOCKER_DOWNLOAD_MIRROR:-}}"
APT_MIRROR="${OPT_APT_MIRROR:-${APT_MIRROR:-}}"
DOCKER_PULL_TIMEOUT_SECONDS="${OPT_DOCKER_PULL_TIMEOUT:-${DOCKER_PULL_TIMEOUT_SECONDS:-180}}"
[[ "${DOCKER_PULL_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
	|| { printf 'ERROR: DOCKER_PULL_TIMEOUT_SECONDS must be a positive integer\n' >&2; exit 2; }
OPT_UBUNTU2404_ARCH="${OPT_UBUNTU2404_ARCH:-${AGENT_UBUNTU2404_ARCH:-}}"
if [[ "${EXPLICIT}" -eq 0 ]]; then
	DO_KOPIA=1
	DO_NAS=1
fi

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

components_label() {
	if [[ "${DO_KOPIA}" -eq 1 && "${DO_NAS}" -eq 1 ]]; then
		printf 'kopia,nas-deps'
	elif [[ "${DO_KOPIA}" -eq 1 ]]; then
		printf 'kopia'
	else
		printf 'nas-deps'
	fi
}

print_config() {
	cat <<CONFIG
components=$(components_label)
version=${AGENT_VERSION}
matrix=${MATRIX}
kopia_mode=${KOPIA_ARTIFACT_MODE}
kopia_git_url=${KOPIA_GIT_URL}
kopia_ref=${KOPIA_GIT_REF}
kopia_version=${KOPIA_VERSION}
github_download_mirror=${GITHUB_DOWNLOAD_MIRROR:-<official>}
docker_download_mirror=${DOCKER_DOWNLOAD_MIRROR:-<official>}
apt_mirror=${APT_MIRROR:-<official>}
ubuntu2404_arch=${OPT_UBUNTU2404_ARCH:-<from-matrix>}
force=${FORCE}
force_pull=${FORCE_PULL}
docker_pull_timeout_seconds=${DOCKER_PULL_TIMEOUT_SECONDS}
kopia_output=${KOPIA_BUILD_DIR}
nas_output=${NAS_DEPS_BASE}/ubuntu-{20.04,22.04,24.04}
CONFIG
}

setup_log_file() {
	[[ "${HFL_PARENT_SESSION:-0}" != "1" ]] || return 0
	[[ "${HFL_LOG_TEE_ACTIVE:-0}" != "1" ]] || return 0
	[[ -n "${LOG_FILE}" ]] || return 0
	mkdir -p "$(dirname "${LOG_FILE}")"
	exec > >(tee -a "${LOG_FILE}") 2>&1
}

fetch_kopia() {
	local -a args=(
		--kopia-mode "${KOPIA_ARTIFACT_MODE}"
		--kopia-git-url "${KOPIA_GIT_URL}"
		--kopia-ref "${KOPIA_GIT_REF}"
		--matrix "${MATRIX}"
	)
	[[ "${FORCE}" -eq 0 ]] || args+=(--force)
	[[ -z "${GITHUB_DOWNLOAD_MIRROR}" ]] || args+=(--github-download-mirror "${GITHUB_DOWNLOAD_MIRROR}")
	[[ -z "${GITHUB_TOKEN}" ]] || args+=(--github-token "${GITHUB_TOKEN}")
	log_step "Preparing the unified Kopia artifact matrix"
	"${REPO_ROOT}/tools/kopia/prepare.sh" "${args[@]}"
	log_ok "Kopia binaries are ready under ${KOPIA_DIST_DIR}"
}

matrix_has_linux_arch() {
	local want=$1
	local item goos goarch
	for item in ${MATRIX}; do
		IFS=: read -r goos goarch <<<"${item}"
		[[ "${goos}" == "linux" && "${goarch}" == "${want}" ]] && return 0
	done
	return 1
}

resolve_nas_arches() {
	local -a arches=()
	local arch_filter="${OPT_UBUNTU2404_ARCH:-}"

	if [[ -n "${arch_filter}" ]]; then
		case "${arch_filter}" in
		all)
			matrix_has_linux_arch amd64 && arches+=(amd64)
			matrix_has_linux_arch arm64 && arches+=(arm64)
			if [[ ${#arches[@]} -eq 0 ]]; then
				arches=(amd64 arm64)
			fi
			;;
		amd64 | arm64)
			arches=("${arch_filter}")
			;;
		*)
			log_fail "Invalid Ubuntu 24.04 architecture ${arch_filter}; use amd64, arm64, or all" 2
			;;
		esac
	else
		matrix_has_linux_arch amd64 && arches+=(amd64)
		matrix_has_linux_arch arm64 && arches+=(arm64)
		if [[ ${#arches[@]} -eq 0 ]]; then
			arches=(amd64 arm64)
		fi
	fi

	sort_nas_arches "${arches[@]}"
}

sort_nas_arches() {
	local -a raw=("$@") sorted=()
	local a r
	for a in amd64 arm64; do
		for r in "${raw[@]}"; do
			if [[ "${r}" == "${a}" ]]; then
				sorted+=("${a}")
			fi
		done
	done
	((${#sorted[@]} > 0)) || return 0
	printf '%s\n' "${sorted[@]}"
}

docker_platform_for_arch() {
	case "$1" in
	amd64) echo "linux/amd64" ;;
	arm64) echo "linux/arm64" ;;
	esac
}

NAS_DOCKER_TIMEOUT=900
NAS_DOCKER_TIMEOUT_ARM64=1800
NAS_DEPS_MIN_DEBS=30
NAS_IMAGE="ubuntu:24.04"
NAS_UBUNTU_RELEASE="24.04"

hfl_nas_docker_script() {
	cat <<'NAS_SCRIPT'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
arch="__ARCH__"
dest="/out/${arch}"
apt_mirror="__APT_MIRROR__"
work="/tmp/hfl-nas-deps"
mkdir -p "${work}" "${dest}"
chmod 777 "${work}"
cd "${work}"
baseline_status="${work}/dpkg-status.before-bootstrap"
cp /var/lib/dpkg/status "${baseline_status}"

# Preserve an explicitly configured mirror and its scheme. Without one, use
# Ubuntu's official HTTPS endpoints.
apt_mirror_url=""
if [[ -n "${apt_mirror}" ]]; then
  apt_mirror_url="${apt_mirror%/}"
fi

if [[ -n "${apt_mirror_url}" ]]; then
  m="${apt_mirror_url}"
  echo "  apt mirror: ${m} (${arch})"
else
  echo "  apt source: official Ubuntu (HTTPS after CA bootstrap) (${arch})"
fi
for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
  [[ -f "${source_file}" ]] || continue
  if [[ -n "${apt_mirror_url}" ]]; then
    sed -E -i "s|https?://ports.ubuntu.com/ubuntu-ports|${m}/ubuntu-ports|g" "${source_file}"
    sed -E -i "s|https?://archive.ubuntu.com/ubuntu|${m}/ubuntu|g" "${source_file}"
    sed -E -i "s|https?://security.ubuntu.com/ubuntu|${m}/ubuntu|g" "${source_file}"
  fi
done

# Minimal Ubuntu images do not ship ca-certificates. Disable TLS peer checks
# only while installing that package; APT repository signatures remain
# enforced. The next update runs with normal strict TLS verification.
apt_network=(
  apt-get
  -o Acquire::Retries=5
  -o Acquire::http::Timeout=30
  -o Acquire::https::Timeout=30
)
bootstrap_apt=(
  "${apt_network[@]}"
  -o Acquire::https::Verify-Peer=false
  -o Acquire::https::Verify-Host=false
)
echo "  bootstrap: ca-certificates (signed APT metadata; temporary TLS peer bypass)"
if ! "${bootstrap_apt[@]}" update -qq; then
  echo "ERROR: bootstrap apt-get update failed (${arch})" >&2
  exit 1
fi
if ! "${bootstrap_apt[@]}" install -y --no-install-recommends ca-certificates; then
  echo "ERROR: bootstrap ca-certificates install failed (${arch})" >&2
  exit 1
fi
if [[ -z "${apt_mirror_url}" ]]; then
  for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
    [[ -f "${source_file}" ]] || continue
    sed -i 's|http://ports.ubuntu.com/ubuntu-ports|https://ports.ubuntu.com/ubuntu-ports|g' "${source_file}"
    sed -i 's|http://archive.ubuntu.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' "${source_file}"
    sed -i 's|http://security.ubuntu.com/ubuntu|https://security.ubuntu.com/ubuntu|g' "${source_file}"
  done
fi
if ! "${apt_network[@]}" update -qq; then
  echo "ERROR: apt-get update failed after secure source configuration (${arch})" >&2
  exit 1
fi

echo "  download: nfs-common cifs-utils (+dependencies)"
rm -f /var/cache/apt/archives/*.deb 2>/dev/null || true
download_ok=0
for attempt in 1 2 3; do
  if "${apt_network[@]}" \
    -o Dir::State::status="${baseline_status}" \
    install -y --download-only --no-install-recommends nfs-common cifs-utils; then
    download_ok=1
    break
  fi
  echo "WARN: apt dependency download attempt ${attempt}/3 failed; retrying cached remainder" >&2
  sleep $((attempt * 5))
done
if [[ "${download_ok}" -ne 1 ]]; then
  echo "ERROR: apt dependency download failed after 3 attempts (${arch})" >&2
  exit 1
fi

decode_apt_deb_name() {
  local encoded=$1 len i c h decoded=""
  len=${#encoded}
  i=0
  while (( i < len )); do
    c=${encoded:i:1}
    if [[ "${c}" == "%" && $((i + 2)) -lt len ]]; then
      h=${encoded:i+1:2}
      if [[ "${h}" =~ ^[0-9A-Fa-f]{2}$ ]]; then
        decoded+=$(printf "\\x${h}")
        i=$((i + 3))
        continue
      fi
    fi
    decoded+="${c}"
    i=$((i + 1))
  done
  printf '%s' "${decoded}"
}

rm -f "${dest}"/*.deb
shopt -s nullglob
for f in /var/cache/apt/archives/*.deb; do
  base="$(basename "${f}")"
  decoded="$(decode_apt_deb_name "${base}")"
  cp -f "${f}" "${dest}/${decoded}"
done
count="$(find "${dest}" -maxdepth 1 -name "*.deb" | wc -l | tr -d " ")"
if [[ "${count}" -lt 2 ]]; then
  echo "ERROR: expected nfs-common/cifs-utils debs under ${dest}, got ${count}" >&2
  exit 1
fi
if ! compgen -G "${dest}/nfs-common_*.deb" >/dev/null || ! compgen -G "${dest}/cifs-utils_*.deb" >/dev/null; then
  echo "ERROR: missing nfs-common or cifs-utils deb under ${dest}" >&2
  exit 1
fi
echo "  ${count} deb(s) in ${dest}"
NAS_SCRIPT
}

nas_deps_count() {
	local dest=$1
	find "${dest}" -maxdepth 1 -name '*.deb' 2>/dev/null | wc -l | tr -d ' '
}

nas_deps_cached() {
	local dest=$1 count
	[[ -d "${dest}" ]] || return 1
	# Reject URL-encoded leftovers from older fetch.sh (e.g. cifs-utils_2%3a7.0_….deb).
	compgen -G "${dest}/*%*.deb" >/dev/null && return 1
	compgen -G "${dest}/nfs-common_*.deb" >/dev/null || return 1
	compgen -G "${dest}/cifs-utils_*.deb" >/dev/null || return 1
	count="$(nas_deps_count "${dest}")"
	(( count >= NAS_DEPS_MIN_DEBS )) || return 1
}

write_nas_manifest() {
	local dest=$1
	local arch=$2
	command -v python3 >/dev/null 2>&1 || log_fail "python3 is required to write NAS dependency metadata" 2
	ARCH_VALUE="${arch}" APT_SOURCE="${APT_MIRROR:-official}" IMAGE_SOURCE="${NAS_IMAGE}" \
		UBUNTU_RELEASE_VALUE="${NAS_UBUNTU_RELEASE}" \
		python3 - "${dest}" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
files = {}
for path in sorted(root.glob("*.deb")):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files[path.name] = f"sha256:{digest}"
payload = {
    "schema": 1,
    "ubuntu_release": os.environ["UBUNTU_RELEASE_VALUE"],
    "arch": os.environ["ARCH_VALUE"],
    "container_image": os.environ["IMAGE_SOURCE"],
    "apt_source": os.environ["APT_SOURCE"],
    "files": files,
}
temporary = root / "MANIFEST.json.tmp"
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(root / "MANIFEST.json")
PY
	log_ok "Wrote ${dest}/MANIFEST.json"
}

nas_manifest_valid() {
	local dest=$1
	[[ -f "${dest}/MANIFEST.json" ]] || return 1
	python3 - "${dest}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
try:
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    expected = manifest["files"]
except (OSError, KeyError, json.JSONDecodeError, TypeError):
    raise SystemExit(1)
actual = {
    path.name: f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    for path in sorted(root.glob("*.deb"))
}
raise SystemExit(0 if actual == expected else 1)
PY
}

normalize_docker_mirror_host() {
	local mirror="${1:-}"
	mirror="${mirror#https://}"
	mirror="${mirror#http://}"
	mirror="${mirror%/}"
	printf '%s' "${mirror}"
}

pull_nas_image() {
	local platform=$1
	local mirror_host mirrored official="ubuntu:${NAS_UBUNTU_RELEASE}"

	mirror_host="$(normalize_docker_mirror_host "${DOCKER_DOWNLOAD_MIRROR:-}")"
	mirrored=""
	if [[ -n "${mirror_host}" ]]; then
		mirrored="${mirror_host}/library/ubuntu:${NAS_UBUNTU_RELEASE}"
	fi

	if [[ "${FORCE_PULL}" -eq 0 ]]; then
		if [[ -n "${mirrored}" ]] && nas_image_matches_platform "${mirrored}" "${platform}"; then
			NAS_IMAGE="${mirrored}"
			log_skip "Using local ${NAS_IMAGE} for ${platform}"
			return 0
		fi
		if nas_image_matches_platform "${official}" "${platform}"; then
			NAS_IMAGE="${official}"
			log_skip "Using local ${NAS_IMAGE} for ${platform}"
			return 0
		fi
	fi

	if [[ -n "${mirrored}" ]]; then
		log_step "Pulling ${mirrored} for ${platform} (timeout=${DOCKER_PULL_TIMEOUT_SECONDS}s)"
		if timeout --foreground "${DOCKER_PULL_TIMEOUT_SECONDS}s" \
			docker pull --platform "${platform}" "${mirrored}"; then
			NAS_IMAGE="${mirrored}"
			return 0
		fi
		log_warn "Docker mirror pull failed; trying the official registry"
	fi

	log_step "Pulling ${official} for ${platform} (timeout=${DOCKER_PULL_TIMEOUT_SECONDS}s)"
	if timeout --foreground "${DOCKER_PULL_TIMEOUT_SECONDS}s" \
		docker pull --platform "${platform}" "${official}"; then
		NAS_IMAGE="${official}"
		return 0
	fi
	if nas_image_matches_platform "${official}" "${platform}"; then
		NAS_IMAGE="${official}"
		log_warn "Registry refresh failed; using existing local ${NAS_IMAGE}"
		return 0
	fi
	if [[ -n "${mirrored}" ]] && nas_image_matches_platform "${mirrored}" "${platform}"; then
		NAS_IMAGE="${mirrored}"
		log_warn "Registry refresh failed; using existing local ${NAS_IMAGE}"
		return 0
	fi

	log_warn "Failed to pull ubuntu:${NAS_UBUNTU_RELEASE} for ${platform}"
	log_info "Optional mirror example: --docker-download-mirror docker.m.daocloud.io --apt-mirror https://mirrors.tuna.tsinghua.edu.cn"
	return 1
}

nas_image_matches_platform() {
	local image=$1 platform=$2 expected_arch actual
	case "${platform}" in
	linux/amd64) expected_arch=amd64 ;;
	linux/arm64) expected_arch=arm64 ;;
	*) return 1 ;;
	esac
	actual="$(docker image inspect "${image}" --format '{{.Os}}/{{.Architecture}}' 2>/dev/null || true)"
	[[ "${actual}" == "linux/${expected_arch}" ]]
}

fetch_nas_deps() {
	local ubuntu_release=$1
	NAS_UBUNTU_RELEASE="${ubuntu_release}"
	NAS_IMAGE="ubuntu:${ubuntu_release}"
	local out_root="${NAS_DEPS_BASE}/ubuntu-${ubuntu_release}"

	fetch_arch_docker() {
		local arch=$1
		local platform
		platform="$(docker_platform_for_arch "${arch}")"

		local dest="${out_root}/${arch}"
		mkdir -p "${dest}"

		if nas_deps_cached "${dest}" && [[ "${FORCE}" -eq 0 ]]; then
			if nas_manifest_valid "${dest}"; then
				log_skip "Verified cached NAS dependencies for ${arch} ($(nas_deps_count "${dest}") packages)"
				return 0
			fi
			log_warn "Cached NAS dependency manifest is missing or invalid for ${arch}; refreshing"
		fi

		if [[ "${FORCE}" -eq 1 ]]; then
			rm -f "${dest}"/*.deb 2>/dev/null || true
		elif compgen -G "${dest}/*.deb" >/dev/null; then
			log_warn "Incomplete NAS dependency cache under ${dest}; refreshing"
			rm -f "${dest}"/*.deb 2>/dev/null || true
		fi

		if ! command -v docker >/dev/null 2>&1; then
			log_warn "Docker is required to fetch Ubuntu NAS dependencies for ${arch}"
			return 2
		fi
		command -v timeout >/dev/null 2>&1 \
			|| { log_warn "timeout is required to pull NAS dependency images"; return 2; }

		if ! pull_nas_image "${platform}"; then
			return 1
		fi

		if ! docker run --rm --pull=never --platform "${platform}" "${NAS_IMAGE}" true >/dev/null 2>&1; then
			log_warn "Docker platform ${platform} is unavailable on this host"
			if [[ "${arch}" == "arm64" ]]; then
				log_info "arm64 on amd64 requires QEMU binfmt; install it explicitly before retrying"
			fi
			return 2
		fi

		log_step "Fetching NAS dependencies in ${NAS_IMAGE} for ${arch}"
		local docker_timeout="${NAS_DOCKER_TIMEOUT}"
		if [[ "${arch}" == "arm64" ]]; then
			docker_timeout="${NAS_DOCKER_TIMEOUT_ARM64}"
			log_info "NAS dependency timeout: ${docker_timeout}s (arm64 under QEMU can be slow)"
		else
			log_info "NAS dependency timeout: ${docker_timeout}s"
		fi

		local container_name="hfl-nas-deps-${arch}-$$"
		local inner_script script_file exit_code
		inner_script="$(hfl_nas_docker_script)"
		inner_script="${inner_script//__ARCH__/${arch}}"
		inner_script="${inner_script//__APT_MIRROR__/${APT_MIRROR:-}}"

		script_file="$(mktemp)"
		printf '%s\n' "${inner_script}" > "${script_file}"

		hfl_nas_docker_stop() {
			rm -f "${script_file}"
			docker rm -f "${container_name}" >/dev/null 2>&1 || true
			trap - INT TERM
		}

		hfl_nas_docker_interrupt() {
			log_warn "Interrupted; stopping ${container_name}"
			hfl_nas_docker_stop
			exit 130
		}
		trap hfl_nas_docker_interrupt INT TERM

		docker rm -f "${container_name}" >/dev/null 2>&1 || true
		if ! docker run -d --init --name "${container_name}" \
			--platform "${platform}" \
			-e DEBIAN_FRONTEND=noninteractive \
			-e "APT_MIRROR=${APT_MIRROR:-}" \
			"${NAS_IMAGE}" \
			sleep infinity; then
			hfl_nas_docker_stop
			log_warn "Failed to start NAS fetch container for ${arch}"
			return 1
		fi

		if ! docker cp "${script_file}" "${container_name}:/tmp/fetch-nas-debs.sh"; then
			hfl_nas_docker_stop
			log_warn "Failed to copy NAS fetch script into the container for ${arch}"
			return 1
		fi

		exit_code=0
		set +e
		if command -v timeout >/dev/null 2>&1; then
			timeout "${docker_timeout}" docker exec "${container_name}" bash /tmp/fetch-nas-debs.sh
			exit_code=$?
		else
			docker exec "${container_name}" bash /tmp/fetch-nas-debs.sh
			exit_code=$?
		fi
		set -e

		if [[ "${exit_code}" -eq 124 ]]; then
			log_warn "Docker fetch for ${arch} timed out after ${docker_timeout}s"
			log_info "For slow QEMU builds, retry with --apt-mirror https://mirrors.tuna.tsinghua.edu.cn or select --ubuntu2404-arch amd64"
			hfl_nas_docker_stop
			return 1
		fi

		if [[ "${exit_code}" == "0" ]]; then
			rm -f "${dest}"/*.deb 2>/dev/null || true
			if ! docker cp "${container_name}:/out/${arch}/." "${dest}/"; then
				hfl_nas_docker_stop
				log_warn "Failed to copy NAS debs from the container to ${dest}"
				return 1
			fi
		fi

		hfl_nas_docker_stop

		if [[ "${exit_code}" != "0" ]]; then
			log_warn "NAS fetch container for ${arch} exited with status ${exit_code}"
			log_info "Retry from the repository root with ./src/agent/scripts/fetch-deps.sh --nas-deps --ubuntu2404-arch ${arch} --force --docker-download-mirror ${DOCKER_DOWNLOAD_MIRROR:-docker.m.daocloud.io} --apt-mirror ${APT_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn}"
			return 1
		fi

		if ! nas_deps_cached "${dest}"; then
			log_warn "${arch} fetch finished but ${dest} is incomplete ($(nas_deps_count "${dest}") packages)"
			return 1
		fi
		write_nas_manifest "${dest}" "${arch}"
		log_ok "NAS dependencies for ${arch} are ready ($(nas_deps_count "${dest}") packages under ${dest})"
	}

	local -a arches=()
	local arch
	while IFS= read -r arch; do
		arches+=("${arch}")
	done < <(resolve_nas_arches)

	log_info "NAS dependency target architectures: ${arches[*]} (Docker ubuntu:${ubuntu_release}; host apt is not used)"

	local -a failed_arches=() ok_arches=()
	for arch in "${arches[@]}"; do
		if fetch_arch_docker "${arch}"; then
			ok_arches+=("${arch}")
		else
			failed_arches+=("${arch}")
			if [[ "${arch}" == "amd64" ]] && printf ' %s ' "${arches[*]}" | grep -q ' arm64 '; then
				log_warn "amd64 failed; arm64 under QEMU can be slow, so fix amd64 first or run --ubuntu2404-arch arm64 separately"
			fi
		fi
	done

	if ((${#failed_arches[@]} > 0)); then
		((${#ok_arches[@]} > 0)) && log_info "NAS dependency fetch succeeded for: ${ok_arches[*]}"
		log_fail "NAS dependency fetch failed for: ${failed_arches[*]}"
	fi

	log_ok "NAS dependency packages are ready under ${out_root}"
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
	log_info "Agent dependency fetch session started"
fi
if [[ "${DO_KOPIA}" -eq 1 ]]; then
	fetch_kopia
fi
if [[ "${DO_NAS}" -eq 1 ]]; then
	for ubuntu_release in 20.04 22.04 24.04; do
		fetch_nas_deps "${ubuntu_release}"
	done
fi
