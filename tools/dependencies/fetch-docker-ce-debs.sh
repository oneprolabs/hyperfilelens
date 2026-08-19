#!/usr/bin/env bash
# Download Docker CE debs (+ dependencies) for a supported Ubuntu amd64 host.
#
# Usage:
#   ./tools/dependencies/fetch-docker-ce-debs.sh
#   ./tools/dependencies/fetch-docker-ce-debs.sh --force
#   ./tools/dependencies/fetch-docker-ce-debs.sh --apt-mirror URL --docker-apt-mirror URL
#
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VERSION_FILE="${ROOT}/tools/dependencies/versions/docker-ce.env"
UBUNTU_RELEASE="24.04"
UBUNTU_CODENAME="noble"
OUT_DIR=""
DOCKER_IMAGE=""

FORCE=0
OPT_APT_MIRROR=""
OPT_DOCKER_APT_MIRROR=""
OPT_DOCKER_DOWNLOAD_MIRROR=""
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0

# shellcheck source=../lib/logging.sh
source "${ROOT}/tools/lib/logging.sh"
log() { hfl_log_info "$@"; }
die() { hfl_die "$1" "${2:-1}"; }

require_value() {
	hfl_require_value "$1" "${2:-}"
}

usage() {
	cat <<'USAGE'
Usage: ./tools/dependencies/fetch-docker-ce-debs.sh [options]

Download pinned Docker CE packages for Ubuntu 20.04, 22.04, or 24.04 amd64.

Options:
  --ubuntu-release VERSION  20.04 | 22.04 | 24.04 (default: 24.04)
  --apt-mirror URL           Ubuntu apt mirror (env: APT_MIRROR / BUILD_APT_MIRROR)
  --docker-apt-mirror URL    Docker CE apt base URL (env: DOCKER_APT_MIRROR / BUILD_DOCKER_APT_MIRROR)
  --docker-download-mirror URL  Docker Hub mirror for ubuntu:24.04 (env: DOCKER_DOWNLOAD_MIRROR)
  --force                    Re-download even when cache looks complete
  --log-file FILE            Append runtime logs to FILE
  --verbose                  Enable debug logs
  --print-config             Print effective configuration and exit
  -h, --help                 Show this help

Version pins: tools/dependencies/versions/docker-ce.env

Example for networks with restricted upstream access:
  ./tools/dependencies/fetch-docker-ce-debs.sh --docker-download-mirror docker.m.daocloud.io --apt-mirror https://mirrors.tuna.tsinghua.edu.cn --docker-apt-mirror https://mirrors.aliyun.com/docker-ce/linux/ubuntu

Third-party mirrors are never enabled automatically.
USAGE
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--ubuntu-release)
		require_value "$1" "${2:-}"
		UBUNTU_RELEASE="$2"
		shift 2
		;;
	--force)
		FORCE=1
		shift
		;;
	--apt-mirror)
		require_value "$1" "${2:-}"
		OPT_APT_MIRROR="$2"
		shift 2
		;;
	--docker-apt-mirror)
		require_value "$1" "${2:-}"
		OPT_DOCKER_APT_MIRROR="$2"
		shift 2
		;;
	--docker-download-mirror)
		require_value "$1" "${2:-}"
		OPT_DOCKER_DOWNLOAD_MIRROR="$2"
		shift 2
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
	-h | --help)
		usage
		exit 0
		;;
	*)
		die "unknown argument: $1 (try --help)" 2
		;;
	esac
done

case "${UBUNTU_RELEASE}" in
20.04)
	UBUNTU_CODENAME="focal"
	VERSION_FILE="${ROOT}/tools/dependencies/versions/docker-ce-ubuntu2004.env"
	;;
22.04)
	UBUNTU_CODENAME="jammy"
	VERSION_FILE="${ROOT}/tools/dependencies/versions/docker-ce-ubuntu2204.env"
	;;
24.04) UBUNTU_CODENAME="noble" ;;
*) die "unsupported Ubuntu release ${UBUNTU_RELEASE} (use 20.04, 22.04, or 24.04)" 2 ;;
esac
OUT_DIR="${ROOT}/build/dependencies/docker/ubuntu-${UBUNTU_RELEASE}/amd64"
DOCKER_IMAGE="ubuntu:${UBUNTU_RELEASE}"

APT_MIRROR="${OPT_APT_MIRROR:-${APT_MIRROR:-${BUILD_APT_MIRROR:-}}}"
DOCKER_APT_MIRROR="${OPT_DOCKER_APT_MIRROR:-${DOCKER_APT_MIRROR:-${BUILD_DOCKER_APT_MIRROR:-https://download.docker.com/linux/ubuntu}}}"
DOCKER_DOWNLOAD_MIRROR="${OPT_DOCKER_DOWNLOAD_MIRROR:-${DOCKER_DOWNLOAD_MIRROR:-}}"

[[ -f "${VERSION_FILE}" ]] || die "missing version pin file: ${VERSION_FILE}"

# shellcheck disable=SC1090
source "${VERSION_FILE}"

pin_for_release() {
	printf '%s' "$1" | sed -E \
		"s/~ubuntu\.[0-9]+\.[0-9]+~[A-Za-z0-9]+$/~ubuntu.${UBUNTU_RELEASE}~${UBUNTU_CODENAME}/"
}

ENGINE_VERSION="$(pin_for_release "${ENGINE_VERSION}")"
CLI_VERSION="$(pin_for_release "${CLI_VERSION}")"
CONTAINERD_VERSION="$(pin_for_release "${CONTAINERD_VERSION}")"
COMPOSE_PLUGIN_VERSION="$(pin_for_release "${COMPOSE_PLUGIN_VERSION}")"

: "${ENGINE_VERSION:?ENGINE_VERSION missing in ${VERSION_FILE}}"
: "${CLI_VERSION:?CLI_VERSION missing in ${VERSION_FILE}}"
: "${CONTAINERD_VERSION:?CONTAINERD_VERSION missing in ${VERSION_FILE}}"
: "${COMPOSE_PLUGIN_VERSION:?COMPOSE_PLUGIN_VERSION missing in ${VERSION_FILE}}"
: "${MIN_ENGINE_VERSION:=24.0.0}"

cache_complete() {
	local dir=${1:-${OUT_DIR}}
	[[ -d "${dir}" ]] || return 1
	[[ -f "${dir}/Packages.gz" ]] || return 1
	compgen -G "${dir}/docker-ce_*.deb" >/dev/null || return 1
	compgen -G "${dir}/docker-compose-plugin_*.deb" >/dev/null || return 1
	compgen -G "${dir}/containerd.io_*.deb" >/dev/null || return 1
	local count
	count="$(find "${dir}" -maxdepth 1 -name '*.deb' | wc -l | tr -d ' ')"
	(( count >= 10 )) || return 1
}

print_config() {
	cat <<EOF
output_dir=${OUT_DIR}
ubuntu_release=${UBUNTU_RELEASE}
ubuntu_codename=${UBUNTU_CODENAME}
version_file=${VERSION_FILE}
base_image=${DOCKER_IMAGE}
engine_version=${ENGINE_VERSION}
cli_version=${CLI_VERSION}
containerd_version=${CONTAINERD_VERSION}
compose_plugin_version=${COMPOSE_PLUGIN_VERSION}
docker_download_mirror=${DOCKER_DOWNLOAD_MIRROR:-<official>}
docker_apt_mirror=${DOCKER_APT_MIRROR}
apt_mirror=${APT_MIRROR:-<official>}
force=${FORCE}
log_file=${LOG_FILE:-<none>}
verbose=${VERBOSE}
EOF
}

hfl_logging_configure docker-dependencies "${LOG_FILE}" "${VERBOSE}"
if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
	print_config
	exit 0
fi
hfl_logging_start
trap 'rc=$?; hfl_logging_finish "${rc}"' EXIT
trap 'exit 130' INT TERM

if [[ "${FORCE}" -eq 0 ]] && cache_complete; then
	log "Using cached host debs in ${OUT_DIR} ($(find "${OUT_DIR}" -maxdepth 1 -name '*.deb' | wc -l | tr -d ' ') packages)"
	exit 0
fi

command -v docker >/dev/null 2>&1 || die "docker not found (required to fetch host debs in ${DOCKER_IMAGE} container)" 2
docker info >/dev/null 2>&1 || die "docker daemon not reachable" 2
command -v python3 >/dev/null 2>&1 || die "python3 not found" 2

mkdir -p "$(dirname "${OUT_DIR}")"

log "Fetching Docker CE debs (ubuntu ${UBUNTU_RELEASE} amd64)"
log "  engine=${ENGINE_VERSION}"
log "  compose-plugin=${COMPOSE_PLUGIN_VERSION}"
log "  docker-apt=${DOCKER_APT_MIRROR}"
[[ -n "${APT_MIRROR}" ]] && log "  apt-mirror=${APT_MIRROR}"

container_name="hfl-host-docker-debs-$$"
script_file="$(mktemp)"
staging_dir="${OUT_DIR}.part.$$"
cleanup_fetch_container() {
	rm -f "${script_file}"
	rm -rf "${staging_dir}"
	docker rm -f "${container_name}" >/dev/null 2>&1 || true
}
finish_fetch() {
	local rc=$?
	trap - EXIT
	cleanup_fetch_container
	hfl_logging_finish "${rc}"
	exit "${rc}"
}
trap finish_fetch EXIT
trap 'exit 130' INT TERM

cat > "${script_file}" <<'INNER'
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
mkdir -p /out
baseline_status="/tmp/hfl-dpkg-status.before-bootstrap"
cp /var/lib/dpkg/status "${baseline_status}"

apt_mirror_url=""
if [[ -n "${APT_MIRROR:-}" ]]; then
  apt_mirror_url="${APT_MIRROR%/}"
fi

if [[ -n "${apt_mirror_url}" ]]; then
  m="${apt_mirror_url}"
  echo "  using Ubuntu apt mirror: ${m}"
else
  echo "  using official Ubuntu sources (HTTPS after CA bootstrap)"
fi
for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
  [[ -f "${source_file}" ]] || continue
  if [[ -n "${apt_mirror_url}" ]]; then
    sed -E -i "s|https?://ports.ubuntu.com/ubuntu-ports|${m}/ubuntu-ports|g" "${source_file}"
    sed -E -i "s|https?://archive.ubuntu.com/ubuntu|${m}/ubuntu|g" "${source_file}"
    sed -E -i "s|https?://security.ubuntu.com/ubuntu|${m}/ubuntu|g" "${source_file}"
  fi
done

# Bootstrap the CA bundle through signed APT metadata, then immediately return
# to strict TLS verification for every subsequent package and key download.
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
"${bootstrap_apt[@]}" update -qq
"${bootstrap_apt[@]}" install -y --no-install-recommends ca-certificates
if [[ -z "${apt_mirror_url}" ]]; then
  for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
    [[ -f "${source_file}" ]] || continue
    sed -i 's|http://ports.ubuntu.com/ubuntu-ports|https://ports.ubuntu.com/ubuntu-ports|g' "${source_file}"
    sed -i 's|http://archive.ubuntu.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' "${source_file}"
    sed -i 's|http://security.ubuntu.com/ubuntu|https://security.ubuntu.com/ubuntu|g' "${source_file}"
  done
fi
"${apt_network[@]}" update -qq

bootstrap_tools_ok=0
for attempt in 1 2 3; do
  if "${apt_network[@]}" install -y --no-install-recommends curl gnupg apt-utils; then
    bootstrap_tools_ok=1
    break
  fi
  echo "WARN: host dependency tool download attempt ${attempt}/3 failed; retrying cached remainder" >&2
  sleep $((attempt * 5))
done
[[ "${bootstrap_tools_ok}" -eq 1 ]] || {
  echo "ERROR: host dependency tools failed after 3 attempts" >&2
  exit 1
}

docker_apt="${DOCKER_APT_MIRROR:-https://download.docker.com/linux/ubuntu}"
docker_apt="${docker_apt%/}"
install -m 0755 -d /etc/apt/keyrings

fetch_docker_gpg() {
  local base="${1%/}"
  curl -fsSL --connect-timeout 30 --max-time 120 --retry 3 --retry-delay 2 \
    "${base}/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
}

docker_apt_candidates=()
append_candidate() {
  local candidate="${1%/}"
  [[ -n "${candidate}" ]] || return 0
  local existing
  for existing in "${docker_apt_candidates[@]}"; do
    [[ "${existing}" == "${candidate}" ]] && return 0
  done
  docker_apt_candidates+=("${candidate}")
}
append_candidate "${docker_apt}"
append_candidate "https://download.docker.com/linux/ubuntu"

gpg_ok=0
for candidate in "${docker_apt_candidates[@]}"; do
  if fetch_docker_gpg "${candidate}"; then
    docker_apt="${candidate}"
    gpg_ok=1
    echo "  using docker apt mirror: ${docker_apt}"
    break
  fi
  echo "  WARN: failed to fetch Docker GPG from ${candidate}" >&2
done
if [[ "${gpg_ok}" -ne 1 ]]; then
  echo "ERROR: could not fetch Docker apt GPG from any mirror (try --docker-apt-mirror)" >&2
  exit 1
fi
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] ${docker_apt} ${UBUNTU_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
"${apt_network[@]}" \
  -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/docker.list \
  -o Dir::Etc::sourceparts=- \
  -o APT::Get::List-Cleanup=0 \
  update -qq

rm -f /var/cache/apt/archives/*.deb
download_ok=0
for attempt in 1 2 3; do
  if "${apt_network[@]}" \
    -o Dir::State::status="${baseline_status}" \
    install -y --download-only --no-install-recommends \
    "containerd.io=${CONTAINERD_VERSION}" \
    "docker-ce-cli=${CLI_VERSION}" \
    "docker-ce=${ENGINE_VERSION}" \
    "docker-compose-plugin=${COMPOSE_PLUGIN_VERSION}"; then
    download_ok=1
    break
  fi
  echo "WARN: Docker deb download attempt ${attempt}/3 failed; retrying cached remainder" >&2
  sleep $((attempt * 5))
done
[[ "${download_ok}" -eq 1 ]] || {
  echo "ERROR: Docker deb download failed after 3 attempts" >&2
  exit 1
}

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

rm -f /out/*.deb
shopt -s nullglob
for f in /var/cache/apt/archives/*.deb; do
  base="$(basename "${f}")"
  decoded="$(decode_apt_deb_name "${base}")"
  cp -f "${f}" "/out/${decoded}"
done

count="$(find /out -maxdepth 1 -name '*.deb' | wc -l | tr -d ' ')"
if (( count < 10 )); then
  echo "ERROR: expected at least 10 debs in /out, got ${count}" >&2
  exit 1
fi
for pkg in docker-ce docker-compose-plugin containerd.io; do
  if ! compgen -G "/out/${pkg}_*.deb" >/dev/null; then
    echo "ERROR: missing ${pkg} deb in /out" >&2
    exit 1
  fi
done

cd /out
apt-ftparchive packages . | gzip -9cn > Packages.gz
echo "  wrote ${count} deb(s) + Packages.gz"
INNER

docker rm -f "${container_name}" >/dev/null 2>&1 || true
if [[ -n "${DOCKER_DOWNLOAD_MIRROR}" ]]; then
	mirror_host="${DOCKER_DOWNLOAD_MIRROR#https://}"
	mirror_host="${mirror_host#http://}"
	mirror_host="${mirror_host%/}"
	mirrored_image="${mirror_host}/library/${DOCKER_IMAGE}"
	if docker pull "${mirrored_image}"; then
		docker tag "${mirrored_image}" "${DOCKER_IMAGE}"
	else
		hfl_log_warn "Docker mirror pull failed; falling back to ${DOCKER_IMAGE}"
		docker pull "${DOCKER_IMAGE}"
	fi
fi
if ! docker image inspect "${DOCKER_IMAGE}" >/dev/null 2>&1; then
	command -v timeout >/dev/null 2>&1 || die "timeout is required to pull ${DOCKER_IMAGE}"
	timeout --foreground 300s docker pull "${DOCKER_IMAGE}" \
		|| die "timed out pulling ${DOCKER_IMAGE}"
fi
if ! docker run -d --init --name "${container_name}" --platform linux/amd64 \
	--pull=never \
	-e DEBIAN_FRONTEND=noninteractive \
	-e APT_MIRROR="${APT_MIRROR}" \
	-e DOCKER_APT_MIRROR="${DOCKER_APT_MIRROR}" \
	-e UBUNTU_CODENAME="${UBUNTU_CODENAME}" \
	-e ENGINE_VERSION="${ENGINE_VERSION}" \
	-e CLI_VERSION="${CLI_VERSION}" \
	-e CONTAINERD_VERSION="${CONTAINERD_VERSION}" \
	-e COMPOSE_PLUGIN_VERSION="${COMPOSE_PLUGIN_VERSION}" \
	"${DOCKER_IMAGE}" \
	sleep infinity; then
	die "failed to start host Docker CE deb fetch container"
fi

if ! docker cp "${script_file}" "${container_name}:/tmp/fetch-host-debs.sh"; then
	die "failed to copy fetch script into container"
fi

exit_code=0
set +e
docker exec "${container_name}" bash /tmp/fetch-host-debs.sh
exit_code=$?
set -e

if [[ "${exit_code}" != "0" ]]; then
	docker logs "${container_name}" >&2 || true
	die "host Docker CE deb fetch container exited ${exit_code}"
fi

mkdir -p "${staging_dir}"
if ! docker cp "${container_name}:/out/." "${staging_dir}/"; then
	die "failed to copy host Docker CE debs from container to ${staging_dir}"
fi

if ! cache_complete "${staging_dir}"; then
	die "host Docker CE deb fetch finished but the staged cache is incomplete"
fi

ENGINE_VERSION_VALUE="${ENGINE_VERSION}" \
	CLI_VERSION_VALUE="${CLI_VERSION}" \
	CONTAINERD_VERSION_VALUE="${CONTAINERD_VERSION}" \
	COMPOSE_PLUGIN_VERSION_VALUE="${COMPOSE_PLUGIN_VERSION}" \
	MIN_ENGINE_VERSION_VALUE="${MIN_ENGINE_VERSION}" \
	MIN_COMPOSE_VERSION_VALUE="${MIN_COMPOSE_VERSION}" \
	python3 - "${staging_dir}" "${VERSION_FILE}" "${UBUNTU_RELEASE}" <<'PY'
import hashlib
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
pins = pathlib.Path(sys.argv[2])
ubuntu_release = sys.argv[3]
packages = []
def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

for path in sorted(root.glob("*.deb")):
    packages.append({
        "file": path.name,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    })
payload = {
    "platform": f"ubuntu-{ubuntu_release}-amd64",
    "version_file": pins.name,
    "versions": {
        "engine": os.environ["ENGINE_VERSION_VALUE"],
        "cli": os.environ["CLI_VERSION_VALUE"],
        "containerd": os.environ["CONTAINERD_VERSION_VALUE"],
        "compose": os.environ["COMPOSE_PLUGIN_VERSION_VALUE"],
        "min_engine": os.environ["MIN_ENGINE_VERSION_VALUE"],
        "min_compose": os.environ["MIN_COMPOSE_VERSION_VALUE"],
    },
    "packages": packages,
}
(root / "MANIFEST.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

old_dir="${OUT_DIR}.old.$$"
rm -rf "${old_dir}"
if [[ -d "${OUT_DIR}" ]]; then
	mv "${OUT_DIR}" "${old_dir}"
fi
if ! mv "${staging_dir}" "${OUT_DIR}"; then
	[[ -d "${old_dir}" ]] && mv "${old_dir}" "${OUT_DIR}"
	die "failed to activate the staged Docker CE dependency cache"
fi
rm -rf "${old_dir}"

count="$(find "${OUT_DIR}" -maxdepth 1 -name '*.deb' | wc -l | tr -d ' ')"
log "Cached ${count} host debs in ${OUT_DIR}"
