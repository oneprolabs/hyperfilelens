#!/usr/bin/env bash
# Build Agent distributions under build/agent/<version>/package/, then publish copies to
# data/media/agent-releases/<version>/ (runtime path for nginx; survives build.sh --clean).
#
# Pipeline (Agent): fetch-deps.sh → build.sh → package.sh
# This script:       fetch + build + package → copy package/* + enroll scripts → media/agent-releases/
#
# Usage:
#   ./tools/agent/publish.sh
#   ./tools/agent/publish.sh --matrix "linux:amd64"
#   ./tools/agent/publish.sh --version 0.1.0
#
set -euo pipefail
umask 022

DEFAULT_MATRIX="linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64"

require_value() {
	hfl_require_value "$1" "${2:-}"
}

usage() {
	cat <<USAGE
Usage: ./tools/agent/publish.sh [options]

Role: fetch-deps.sh → build.sh → package.sh, then publish packages and enrollment binaries.

Options:
  --version VERSION                Release version (default: exact Git tag or 0.1.0; env: RELEASE_VERSION)
  --matrix MATRIX                  os:arch list (default: full matrix; env: AGENT_MATRIX)
  --commit COMMIT                  Full build commit (env: AGENT_COMMIT)
  --bundle KIND                    all | standard | ubuntu2004 | ubuntu2204 | ubuntu2404 (env: AGENT_BUNDLE)
  --ubuntu2404-arch ARCH           NAS deb arch for either Ubuntu bundle: amd64 | arm64 | all
                                   (env: AGENT_UBUNTU2404_ARCH; default: amd64)
  --force-fetch                    Re-download fetch-deps.sh inputs (--force)
  --pull                           Refresh the NAS Ubuntu image instead of using a matching local image
  --releases-dir DIR               Publish target (default: data/media/agent-releases; env: AGENT_RELEASES_DIR)

Build (passed to build.sh):
  --go-proxy URL                   Go module proxy (env: GOPROXY)
  --go-sumdb VALUE                 Go checksum database (env: GOSUMDB)

Fetch (passed to fetch-deps.sh):
  --kopia-mode MODE               build or download
  --kopia-git-url URL             Kopia source repository URL
  --kopia-ref REF                 Kopia release ref in vX.Y.Z form
  --github-download-mirror URL     GitHub Git/release mirror (env: GITHUB_DOWNLOAD_MIRROR)
  --github-token TOKEN             GitHub API token (env: GITHUB_TOKEN)
  --docker-download-mirror URL     Docker Hub mirror for NAS Ubuntu images (env: DOCKER_DOWNLOAD_MIRROR)
  --docker-pull-timeout SECONDS    Timeout for each Docker pull attempt (env: DOCKER_PULL_TIMEOUT_SECONDS)
  --apt-mirror URL                 Ubuntu apt mirror for NAS container (env: APT_MIRROR)

Output:
  --log-file FILE                  Append runtime logs to FILE (env: HFL_LOG_FILE)
  --verbose                        Enable debug logs (env: HFL_LOG_VERBOSE=1)
  --print-config                   Print effective non-secret configuration and exit

  -h, --help                       Show this help

Examples:
  ./tools/agent/publish.sh
  ./tools/agent/publish.sh --bundle standard
  ./tools/agent/publish.sh --bundle standard --version VERSION --matrix MATRIX --force-fetch --releases-dir DIR --github-download-mirror URL --github-token TOKEN
  ./tools/agent/publish.sh --bundle ubuntu2004
  ./tools/agent/publish.sh --bundle ubuntu2204
  ./tools/agent/publish.sh --bundle ubuntu2404
  ./tools/agent/publish.sh --bundle ubuntu2404 --version VERSION --ubuntu2404-arch ARCH --force-fetch --releases-dir DIR --github-download-mirror URL --github-token TOKEN --docker-download-mirror URL --apt-mirror URL
  ./tools/agent/publish.sh --bundle all
  ./tools/agent/publish.sh --bundle all --version VERSION --matrix MATRIX --ubuntu2404-arch ARCH --force-fetch --releases-dir DIR --github-download-mirror URL --github-token TOKEN --docker-download-mirror URL --apt-mirror URL

  # Optional third-party accelerators for networks with restricted upstream access.
  ./tools/agent/publish.sh --github-download-mirror https://ghfast.top --docker-download-mirror docker.m.daocloud.io --apt-mirror https://mirrors.tuna.tsinghua.edu.cn

Mirror examples are not operated by HyperFileLens and are never enabled automatically.
USAGE
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/version.sh
source "${ROOT}/tools/lib/version.sh"
# shellcheck source=../lib/logging.sh
source "${ROOT}/tools/lib/logging.sh"
# shellcheck source=../kopia/common.sh
source "${ROOT}/tools/kopia/common.sh"
kopia_load_config
AGENT_DIR="${ROOT}/src/agent"
BUILD_DIR="${ROOT}/build/agent"
DEFAULT_RELEASES_DIR="${ROOT}/data/media/agent-releases"
BUNDLE="${AGENT_BUNDLE:-all}"
FORCE_FETCH=0
FORCE_PULL=0
UBUNTU2404_ARCH="${AGENT_UBUNTU2404_ARCH:-amd64}"
MATRIX="${AGENT_MATRIX:-${DEFAULT_MATRIX}}"
OPT_VERSION=""
OPT_RELEASES_DIR=""
OPT_MATRIX=""
OPT_COMMIT=""
OPT_BUNDLE=""
OPT_GO_PROXY=""
OPT_GO_SUMDB=""
OPT_GITHUB_DOWNLOAD_MIRROR=""
OPT_GITHUB_TOKEN=""
OPT_DOCKER_DOWNLOAD_MIRROR=""
OPT_DOCKER_PULL_TIMEOUT=""
OPT_APT_MIRROR=""
LOG_FILE="${HFL_LOG_FILE:-}"
VERBOSE="${HFL_LOG_VERBOSE:-0}"
PRINT_CONFIG=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--version)
		require_value "$1" "${2:-}"
		OPT_VERSION="$2"
		shift 2
		;;
	--releases-dir)
		require_value "$1" "${2:-}"
		OPT_RELEASES_DIR="$2"
		shift 2
		;;
	--force-fetch | --refresh-kopia-cache)
		FORCE_FETCH=1
		shift
		;;
	--pull)
		FORCE_PULL=1
		shift
		;;
	--matrix)
		require_value "$1" "${2:-}"
		OPT_MATRIX="$2"
		shift 2
		;;
	--commit)
		require_value "$1" "${2:-}"
		OPT_COMMIT="$2"
		shift 2
		;;
	--bundle)
		require_value "$1" "${2:-}"
		OPT_BUNDLE="$2"
		shift 2
		;;
	--ubuntu2404-arch)
		require_value "$1" "${2:-}"
		UBUNTU2404_ARCH="$2"
		shift 2
		;;
	--go-proxy)
		require_value "$1" "${2:-}"
		OPT_GO_PROXY="$2"
		shift 2
		;;
	--go-sumdb)
		require_value "$1" "${2:-}"
		OPT_GO_SUMDB="$2"
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
	-*)
		hfl_log_fail "Unknown option: $1"
		usage
		exit 2
		;;
	*)
		hfl_log_fail "Unexpected argument: $1"
		usage
		exit 2
		;;
	esac
done

VERSION="$(normalize_artifact_id "${OPT_VERSION:-$(resolve_release_version)}")" || exit $?
COMMIT="${OPT_COMMIT:-${AGENT_COMMIT:-$(resolve_commit_full "${ROOT}")}}"
RELEASES_DIR="${OPT_RELEASES_DIR:-${AGENT_RELEASES_DIR:-${DEFAULT_RELEASES_DIR}}}"
GO_PROXY="${OPT_GO_PROXY:-${GOPROXY:-}}"
GO_SUMDB="${OPT_GO_SUMDB:-${GOSUMDB:-}}"

if [[ -n "${OPT_MATRIX}" ]]; then
	MATRIX="${OPT_MATRIX}"
fi

if [[ -n "${OPT_BUNDLE}" ]]; then
	BUNDLE="${OPT_BUNDLE}"
fi

case "${BUNDLE}" in
all | standard | ubuntu2004 | ubuntu2204 | ubuntu2404) ;;
*)
	hfl_die "Invalid --bundle ${BUNDLE} (use all, standard, ubuntu2004, ubuntu2204, or ubuntu2404)" 2
	;;
esac

GITHUB_DOWNLOAD_MIRROR="${OPT_GITHUB_DOWNLOAD_MIRROR:-${GITHUB_DOWNLOAD_MIRROR:-}}"
GITHUB_TOKEN="${OPT_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
DOCKER_DOWNLOAD_MIRROR="${OPT_DOCKER_DOWNLOAD_MIRROR:-${DOCKER_DOWNLOAD_MIRROR:-}}"
DOCKER_PULL_TIMEOUT_SECONDS="${OPT_DOCKER_PULL_TIMEOUT:-${DOCKER_PULL_TIMEOUT_SECONDS:-180}}"
[[ "${DOCKER_PULL_TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
	|| hfl_die "DOCKER_PULL_TIMEOUT_SECONDS must be a positive integer" 2
APT_MIRROR="${OPT_APT_MIRROR:-${APT_MIRROR:-}}"
case "${KOPIA_ARTIFACT_MODE}" in build | download) ;; *) hfl_die "Invalid Kopia mode: ${KOPIA_ARTIFACT_MODE}" 2 ;; esac
[[ "${KOPIA_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] \
	|| hfl_die "Invalid Kopia ref: ${KOPIA_GIT_REF}" 2
KOPIA_VERSION="${BASH_REMATCH[1]}"

ubuntu2404_matrix() {
	case "${UBUNTU2404_ARCH}" in
	all) echo "linux:amd64 linux:arm64" ;;
	amd64 | arm64) echo "linux:${UBUNTU2404_ARCH}" ;;
	*)
		hfl_die "Invalid --ubuntu2404-arch ${UBUNTU2404_ARCH} (use amd64, arm64, or all)" 2
		;;
	esac
}

if [[ ( "${BUNDLE}" == "ubuntu2004" || "${BUNDLE}" == "ubuntu2204" || "${BUNDLE}" == "ubuntu2404" ) && -z "${OPT_MATRIX}" ]]; then
	MATRIX="$(ubuntu2404_matrix)"
fi

BOOTSTRAP_DIR="${ROOT}/deploy/bootstrap"
BOOTSTRAP_SCRIPTS=(agent-bootstrap-linux.sh agent-bootstrap-macos.sh agent-bootstrap-windows.ps1 gateway-bootstrap-linux.sh)
GATEWAY_BOOTSTRAP_LINUX_SCRIPT=gateway-bootstrap-linux.sh
GATEWAY_SIDECAR_SCRIPT=gateway-install-lensnode-sidecar.sh
GATEWAY_LIFECYCLE_SCRIPT=gateway-lifecycle.sh
GATEWAY_DOCKER_INSTALL_SCRIPT=gateway-install-docker-ubuntu-amd64.sh
GATEWAY_SENTRY_ADAPTER=hfl-sentry-sitecustomize.py
GATEWAY_LENSNODE_IMAGE=lensnode-image-linux-amd64.tar.gz
ENROLL_BOOTSTRAP_DIR="${RELEASES_DIR%/agent-releases}/enroll-bootstrap"
GATEWAY_BOOTSTRAP_DIR="${RELEASES_DIR%/agent-releases}/gateway-bootstrap"

print_config() {
	cat <<EOF
version=${VERSION}
commit=${COMMIT}
bundle=${BUNDLE}
matrix=${MATRIX}
ubuntu2404_arch=${UBUNTU2404_ARCH}
build_dir=${BUILD_DIR}/${VERSION}
releases_dir=${RELEASES_DIR}
force_fetch=${FORCE_FETCH}
force_pull=${FORCE_PULL}
kopia_mode=${KOPIA_ARTIFACT_MODE}
kopia_git_url=${KOPIA_GIT_URL}
kopia_ref=${KOPIA_GIT_REF}
kopia_version=${KOPIA_VERSION}
go_proxy=${GO_PROXY:-<official>}
go_sumdb=${GO_SUMDB:-<official>}
github_download_mirror=${GITHUB_DOWNLOAD_MIRROR:-<official>}
github_token=$(hfl_redact "${GITHUB_TOKEN}")
docker_download_mirror=${DOCKER_DOWNLOAD_MIRROR:-<official>}
docker_pull_timeout_seconds=${DOCKER_PULL_TIMEOUT_SECONDS}
apt_mirror=${APT_MIRROR:-<official>}
log_file=${LOG_FILE:-<none>}
verbose=${VERBOSE}
EOF
}

hfl_logging_configure agent-publish "${LOG_FILE}" "${VERBOSE}"
if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
	print_config
	exit 0
fi
trap 'rc=$?; hfl_logging_finish "${rc}"' EXIT
trap 'exit 130' INT TERM
hfl_logging_start

if [[ -z "${VERSION}" ]]; then
	hfl_die "Could not resolve release version (use --version or git tag v*)" 2
fi

for name in "${BOOTSTRAP_SCRIPTS[@]}"; do
	if [[ ! -f "${BOOTSTRAP_DIR}/${name}" ]]; then
		hfl_die "Missing bootstrap template ${BOOTSTRAP_DIR}/${name}" 3
	fi
done
if [[ ! -f "${BOOTSTRAP_DIR}/${GATEWAY_SIDECAR_SCRIPT}" ]]; then
	hfl_die "Missing bootstrap template ${BOOTSTRAP_DIR}/${GATEWAY_SIDECAR_SCRIPT}" 3
fi
if [[ ! -f "${BOOTSTRAP_DIR}/${GATEWAY_LIFECYCLE_SCRIPT}" ]]; then
	hfl_die "Missing bootstrap template ${BOOTSTRAP_DIR}/${GATEWAY_LIFECYCLE_SCRIPT}" 3
fi
if [[ ! -f "${BOOTSTRAP_DIR}/${GATEWAY_DOCKER_INSTALL_SCRIPT}" ]]; then
	hfl_die "Missing bootstrap template ${BOOTSTRAP_DIR}/${GATEWAY_DOCKER_INSTALL_SCRIPT}" 3
fi
if [[ ! -f "${ROOT}/deploy/installer/sourcelens/${GATEWAY_SENTRY_ADAPTER}" ]]; then
	hfl_die "Missing Sentry privacy adapter ${GATEWAY_SENTRY_ADAPTER}" 3
fi

validate_matrix() {
	local item goos goarch
	for item in ${MATRIX}; do
		IFS=: read -r goos goarch <<<"${item}"
		case "${goos}" in
		linux | darwin)
			case "${goarch}" in
			amd64 | arm64) ;;
			*)
				hfl_die "Unsupported ${goos} arch in matrix: ${item}" 2
				;;
			esac
			;;
		windows)
			if [[ "${goarch}" != "amd64" ]]; then
				hfl_die "Unsupported windows arch in matrix: ${item}" 2
			fi
			;;
		*)
			hfl_die "Unsupported platform in matrix: ${item}" 2
			;;
		esac
	done
}

matrix_has_linux() {
	local item goos goarch
	for item in ${MATRIX}; do
		IFS=: read -r goos goarch <<<"${item}"
		[[ "${goos}" == "linux" ]] && return 0
	done
	return 1
}

archive_ext_for() {
	case "$1" in
	windows) echo "zip" ;;
	*) echo "tar.gz" ;;
	esac
}

ubuntu_archive_matches() {
	local base=$1 flavor=$2
	case "${UBUNTU2404_ARCH}" in
	all) return 0 ;;
	amd64 | arm64)
		[[ "${base}" == *"-linux-${UBUNTU2404_ARCH}-${flavor}.tar.gz" ]]
		;;
	esac
}

fetch_common_args=()
fetch_common_args+=(--kopia-mode "${KOPIA_ARTIFACT_MODE}")
fetch_common_args+=(--kopia-git-url "${KOPIA_GIT_URL}")
fetch_common_args+=(--kopia-ref "${KOPIA_GIT_REF}")
if [[ "${FORCE_FETCH}" -eq 1 ]]; then
	fetch_common_args+=(--force)
fi
if [[ "${FORCE_PULL}" -eq 1 ]]; then
	fetch_common_args+=(--pull)
fi
if [[ -n "${GITHUB_DOWNLOAD_MIRROR}" ]]; then
	fetch_common_args+=(--github-download-mirror "${GITHUB_DOWNLOAD_MIRROR}")
fi
if [[ -n "${GITHUB_TOKEN}" ]]; then
	export GITHUB_TOKEN
fi
if [[ -n "${DOCKER_DOWNLOAD_MIRROR}" ]]; then
	fetch_common_args+=(--docker-download-mirror "${DOCKER_DOWNLOAD_MIRROR}")
fi
fetch_common_args+=(--docker-pull-timeout "${DOCKER_PULL_TIMEOUT_SECONDS}")
if [[ -n "${APT_MIRROR}" ]]; then
	fetch_common_args+=(--apt-mirror "${APT_MIRROR}")
fi

publish_archives() {
	local published=0 archive dest item goos goarch ext base

	mkdir -p "${RELEASES_DIR}/${VERSION}"

	if [[ "${BUNDLE}" == "ubuntu2004" || "${BUNDLE}" == "ubuntu2204" || "${BUNDLE}" == "ubuntu2404" ]]; then
		local flavor="${BUNDLE}"
		shopt -s nullglob
		for archive in "${BUILD_DIR}/${VERSION}/package"/hfl-agent-"${VERSION}"-*-"${flavor}".tar.gz; do
			[[ -f "${archive}" ]] || continue
			base="$(basename "${archive}")"
			ubuntu_archive_matches "${base}" "${flavor}" || continue
			dest="${RELEASES_DIR}/${VERSION}/${base}"
			cp -f "${archive}" "${dest}"
			chmod 644 "${dest}"
			hfl_log_ok "Published ${base}"
			published=$((published + 1))
		done
		shopt -u nullglob
	elif [[ "${BUNDLE}" == "standard" ]]; then
		for item in ${MATRIX}; do
			IFS=: read -r goos goarch <<<"${item}"
			ext="$(archive_ext_for "${goos}")"
			archive="${BUILD_DIR}/${VERSION}/package/hfl-agent-${VERSION}-${goos}-${goarch}.${ext}"
			if [[ ! -f "${archive}" ]]; then
				hfl_die "Missing build archive ${archive}" 3
			fi
			dest="${RELEASES_DIR}/${VERSION}/$(basename "${archive}")"
			cp -f "${archive}" "${dest}"
			chmod 644 "${dest}"
			hfl_log_ok "Published $(basename "${dest}")"
			published=$((published + 1))
		done
	else
		for item in ${MATRIX}; do
			IFS=: read -r goos goarch <<<"${item}"
			ext="$(archive_ext_for "${goos}")"
			archive="${BUILD_DIR}/${VERSION}/package/hfl-agent-${VERSION}-${goos}-${goarch}.${ext}"
			if [[ ! -f "${archive}" ]]; then
				hfl_die "Missing build archive ${archive}" 3
			fi
			dest="${RELEASES_DIR}/${VERSION}/$(basename "${archive}")"
			cp -f "${archive}" "${dest}"
			chmod 644 "${dest}"
			hfl_log_ok "Published $(basename "${dest}")"
			published=$((published + 1))

			if [[ "${goos}" == "linux" ]]; then
				local ubuntu_flavor
				for ubuntu_flavor in ubuntu2004 ubuntu2204 ubuntu2404; do
					archive="${BUILD_DIR}/${VERSION}/package/hfl-agent-${VERSION}-${goos}-${goarch}-${ubuntu_flavor}.tar.gz"
					if [[ -f "${archive}" ]]; then
						dest="${RELEASES_DIR}/${VERSION}/$(basename "${archive}")"
						cp -f "${archive}" "${dest}"
						chmod 644 "${dest}"
						hfl_log_ok "Published $(basename "${dest}")"
						published=$((published + 1))
					fi
				done
			fi
		done
	fi

	if [[ "${published}" -eq 0 ]]; then
		hfl_die "No Agent archives published from ${BUILD_DIR}/${VERSION}/package/" 3
	fi

	hfl_log_step "Copying bootstrap templates"
	for name in "${BOOTSTRAP_SCRIPTS[@]}"; do
		dest="${RELEASES_DIR}/${VERSION}/${name}"
		cp -f "${BOOTSTRAP_DIR}/${name}" "${dest}"
		if [[ "${name}" == *.sh ]]; then
			chmod 755 "${dest}"
		else
			chmod 644 "${dest}"
		fi
		hfl_log_ok "Published ${name}"
	done

	hfl_log_step "Publishing hfl-enroll binaries"
	mkdir -p "${ENROLL_BOOTSTRAP_DIR}"
	for item in ${MATRIX}; do
		IFS=: read -r goos goarch <<<"${item}"
		enroll_name="hfl-enroll-${goos}-${goarch}"
		[[ "${goos}" == "windows" ]] && enroll_name="${enroll_name}.exe"
		src="${BUILD_DIR}/${VERSION}/${goos}/${goarch}/${enroll_name}"
		if [[ ! -f "${src}" ]]; then
			hfl_die "Missing hfl-enroll build output ${src}" 3
		fi
		dest="${ENROLL_BOOTSTRAP_DIR}/${enroll_name}"
		cp -f "${src}" "${dest}"
		chmod 755 "${dest}" 2>/dev/null || true
		hfl_log_ok "Published ${enroll_name}"
	done

	hfl_log_step "Publishing compressed minimal installers"
	MATRIX_VALUE="${MATRIX}" BUILD_ROOT_VALUE="${BUILD_DIR}/${VERSION}" VERSION_VALUE="${VERSION}" \
		OUTPUT_VALUE="${ENROLL_BOOTSTRAP_DIR}" python3 - <<'PY'
import hashlib
import json
import os
import tarfile
import zipfile
from pathlib import Path

matrix = os.environ["MATRIX_VALUE"].split()
build_root = Path(os.environ["BUILD_ROOT_VALUE"])
output = Path(os.environ["OUTPUT_VALUE"])
version = os.environ["VERSION_VALUE"]
version_output = output / version
version_output.mkdir(parents=True, exist_ok=True)
manifest = {"schema_version": 1, "artifacts": {}}
max_installer_bytes = 3_670_016

for item in matrix:
    goos, goarch = item.split(":", 1)
    binary_name = f"hfl-enroll-{goos}-{goarch}"
    if goos == "windows":
        binary_name += ".exe"
    source = build_root / goos / goarch / binary_name
    if not source.is_file():
        raise SystemExit(f"missing enrollment binary: {source}")
    if goos == "windows":
        filename = f"hfl-installer-{goos}-{goarch}.zip"
        with zipfile.ZipFile(version_output / filename, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(source, "hfl-enroll.exe")
    else:
        filename = f"hfl-installer-{goos}-{goarch}.tar.gz"
        with tarfile.open(version_output / filename, "w:gz", compresslevel=9) as archive:
            info = archive.gettarinfo(str(source), "hfl-enroll")
            info.mode = 0o755
            with source.open("rb") as handle:
                archive.addfile(info, handle)
    artifact = version_output / filename
    if artifact.stat().st_size > max_installer_bytes:
        raise SystemExit(
            f"minimal installer exceeds 3.5 MiB: {filename} "
            f"({artifact.stat().st_size} bytes)"
        )
    manifest["artifacts"][f"{goos}-{goarch}"] = {
        "filename": f"{version}/{filename}",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "size": artifact.stat().st_size,
    }

(output / "INSTALLER_MANIFEST.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
	find "${ENROLL_BOOTSTRAP_DIR}/${VERSION}" -type f -name 'hfl-installer-*' -exec chmod 644 {} +
	chmod 644 \
		"${ENROLL_BOOTSTRAP_DIR}/INSTALLER_MANIFEST.json"
	hfl_log_ok "Published compressed minimal installers"

	hfl_log_step "Publishing gateway sidecar bootstrap"
	mkdir -p "${GATEWAY_BOOTSTRAP_DIR}"
	gateway_bootstrap_src="${BOOTSTRAP_DIR}/${GATEWAY_BOOTSTRAP_LINUX_SCRIPT}"
	cp -f "${gateway_bootstrap_src}" "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_BOOTSTRAP_LINUX_SCRIPT}"
	chmod 755 "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_BOOTSTRAP_LINUX_SCRIPT}"
	hfl_log_ok "Published ${GATEWAY_BOOTSTRAP_LINUX_SCRIPT}"
	sidecar_src="${BOOTSTRAP_DIR}/${GATEWAY_SIDECAR_SCRIPT}"
	cp -f "${sidecar_src}" "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_SIDECAR_SCRIPT}"
	chmod 755 "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_SIDECAR_SCRIPT}"
	hfl_log_ok "Published ${GATEWAY_SIDECAR_SCRIPT}"
	lifecycle_src="${BOOTSTRAP_DIR}/${GATEWAY_LIFECYCLE_SCRIPT}"
	cp -f "${lifecycle_src}" "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_LIFECYCLE_SCRIPT}"
	chmod 755 "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_LIFECYCLE_SCRIPT}"
	hfl_log_ok "Published ${GATEWAY_LIFECYCLE_SCRIPT}"
	docker_install_src="${BOOTSTRAP_DIR}/${GATEWAY_DOCKER_INSTALL_SCRIPT}"
	cp -f "${docker_install_src}" "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_DOCKER_INSTALL_SCRIPT}"
	chmod 755 "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_DOCKER_INSTALL_SCRIPT}"
	hfl_log_ok "Published ${GATEWAY_DOCKER_INSTALL_SCRIPT}"
	privacy_adapter_src="${ROOT}/deploy/installer/sourcelens/${GATEWAY_SENTRY_ADAPTER}"
	cp -f "${privacy_adapter_src}" "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_SENTRY_ADAPTER}"
	chmod 644 "${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_SENTRY_ADAPTER}"
	hfl_log_ok "Published ${GATEWAY_SENTRY_ADAPTER}"
	local ubuntu_release release_id host_debs_dir docker_debs_archive
	for ubuntu_release in 20.04 22.04 24.04; do
		case "${ubuntu_release}" in 20.04) release_id=2004 ;; 22.04) release_id=2204 ;; 24.04) release_id=2404 ;; esac
		host_debs_dir="${ROOT}/build/dependencies/docker/ubuntu-${ubuntu_release}/amd64"
		docker_debs_archive="docker-debs-ubuntu${release_id}-amd64.tar.gz"
		if [[ -d "${host_debs_dir}" ]] && compgen -G "${host_debs_dir}/*.deb" >/dev/null; then
			tar -czf "${GATEWAY_BOOTSTRAP_DIR}/${docker_debs_archive}" -C "${host_debs_dir}" .
			chmod 644 "${GATEWAY_BOOTSTRAP_DIR}/${docker_debs_archive}"
			hfl_log_ok "Published ${docker_debs_archive}"
		else
			hfl_log_skip "${docker_debs_archive}: cached debs not found; run tools/dependencies/fetch-docker-ce-debs.sh --ubuntu-release ${ubuntu_release}"
		fi
	done
	lensnode_image_dest="${GATEWAY_BOOTSTRAP_DIR}/${GATEWAY_LENSNODE_IMAGE}"
	if [[ -f "${lensnode_image_dest}" ]]; then
		hfl_log_ok "${GATEWAY_LENSNODE_IMAGE} is already in place"
	else
		hfl_log_skip "${GATEWAY_LENSNODE_IMAGE}: enable SourceLens in dev/stack.sh or release/build.sh"
	fi
}

validate_matrix

hfl_log_step "Publishing HyperFileLens Agent releases"
hfl_log_info "Version: ${VERSION}"
hfl_log_info "Build directory: ${BUILD_DIR}/${VERSION}/package/"
hfl_log_info "Publish directory: ${RELEASES_DIR}/${VERSION}/"
hfl_log_info "Matrix: ${MATRIX}"
hfl_log_info "Commit: ${COMMIT}"
hfl_log_info "Bundle: ${BUNDLE}"
if [[ "${BUNDLE}" != "standard" ]]; then
	hfl_log_info "Ubuntu offline bundle architecture: ${UBUNTU2404_ARCH}"
fi

if [[ "${BUNDLE}" != "standard" ]] && matrix_has_linux; then
	hfl_log_step "Fetching Ubuntu 20.04/22.04/24.04 NAS dependency debs"
	HFL_PARENT_SESSION=1 "${AGENT_DIR}/scripts/fetch-deps.sh" --nas-deps "${fetch_common_args[@]}" \
		--version "${VERSION}" \
		--matrix "${MATRIX}" \
		--ubuntu2404-arch "${UBUNTU2404_ARCH}"
fi

hfl_log_step "Building Agent"
build_args=(--release \
	--version "${VERSION}" \
	--matrix "${MATRIX}" \
	--commit "${COMMIT}")
[[ -n "${GO_PROXY}" ]] && build_args+=(--go-proxy "${GO_PROXY}")
[[ -n "${GO_SUMDB}" ]] && build_args+=(--go-sumdb "${GO_SUMDB}")
HFL_PARENT_SESSION=1 "${AGENT_DIR}/scripts/build.sh" "${build_args[@]}"

hfl_log_step "Preparing the unified Kopia artifact matrix"
HFL_PARENT_SESSION=1 "${AGENT_DIR}/scripts/fetch-deps.sh" --kopia "${fetch_common_args[@]}" \
	--version "${VERSION}" \
	--matrix "${MATRIX}"

hfl_log_step "Packaging distribution archives"
package_args=(--version "${VERSION}" --matrix "${MATRIX}" --commit "${COMMIT}" --bundle "${BUNDLE}")
if [[ "${BUNDLE}" != "standard" ]] && matrix_has_linux; then
	package_args+=(--ubuntu2404-arch "${UBUNTU2404_ARCH}")
fi
HFL_PARENT_SESSION=1 "${AGENT_DIR}/scripts/package.sh" "${package_args[@]}"

if [[ ! -d "${BUILD_DIR}/${VERSION}/package" ]]; then
	hfl_die "Missing package output ${BUILD_DIR}/${VERSION}/package/" 3
fi

publish_archives

hfl_log_ok "Agent artifacts published for version ${VERSION}"
