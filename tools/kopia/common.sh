#!/usr/bin/env bash
set -euo pipefail

_hfl_kopia_common_loaded="${_hfl_kopia_common_loaded:-}"
if [[ -n "${_hfl_kopia_common_loaded}" ]]; then
	return 0 2>/dev/null || exit 0
fi
_hfl_kopia_common_loaded=1

KOPIA_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KOPIA_ROOT="$(cd "${KOPIA_TOOLS_DIR}/../.." && pwd)"
KOPIA_DEFAULTS_FILE="${KOPIA_TOOLS_DIR}/defaults.env"
KOPIA_BUILD_DIR="${KOPIA_ROOT}/build/kopia"
KOPIA_SOURCE_DIR="${KOPIA_BUILD_DIR}/source"
KOPIA_DIST_DIR="${KOPIA_BUILD_DIR}/dist"
KOPIA_INFO_FILE="${KOPIA_BUILD_DIR}/KOPIA_INFO.json"
KOPIA_PATCH_FILES=(
	"${KOPIA_TOOLS_DIR}/patches/0001-add-s3-url-style.patch"
	"${KOPIA_TOOLS_DIR}/patches/0002-add-structured-progress.patch"
	"${KOPIA_TOOLS_DIR}/patches/0003-disable-managed-dot-ignore.patch"
	"${KOPIA_TOOLS_DIR}/patches/0004-snapshot-estimator-best-effort-errors.patch"
	"${KOPIA_TOOLS_DIR}/patches/0005-add-entry-summary.patch"
)
KOPIA_DEFAULT_MATRIX="linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64"

kopia_load_config() {
	# shellcheck disable=SC1090
	source "${KOPIA_DEFAULTS_FILE}"
	case "${KOPIA_ARTIFACT_MODE}" in
	build | download) ;;
	*) printf 'ERROR: invalid Kopia artifact mode: %s (use build or download)\n' "${KOPIA_ARTIFACT_MODE}" >&2; return 2 ;;
	esac
	[[ "${KOPIA_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || {
		printf 'ERROR: invalid Kopia Git ref: %s (expected vX.Y.Z)\n' "${KOPIA_GIT_REF}" >&2
		return 2
	}
	KOPIA_VERSION="${BASH_REMATCH[1]}"
	export KOPIA_ARTIFACT_MODE KOPIA_GIT_URL KOPIA_GIT_REF KOPIA_VERSION
}

kopia_validate_matrix() {
	local matrix=$1 entry seen=" "
	[[ -n "${matrix//[[:space:]]/}" ]] || { printf 'ERROR: Kopia matrix cannot be empty\n' >&2; return 2; }
	for entry in ${matrix}; do
		case "${entry}" in
		linux:amd64 | linux:arm64 | darwin:amd64 | darwin:arm64 | windows:amd64) ;;
		*) printf 'ERROR: unsupported Kopia matrix entry: %s\n' "${entry}" >&2; return 2 ;;
		esac
		[[ "${seen}" != *" ${entry} "* ]] || { printf 'ERROR: duplicate Kopia matrix entry: %s\n' "${entry}" >&2; return 2; }
		seen+="${entry} "
	done
}

kopia_binary_path() {
	local goos=$1 goarch=$2 suffix=""
	[[ "${goos}" != windows ]] || suffix=".exe"
	printf '%s/%s/%s/kopia%s' "${KOPIA_DIST_DIR}" "${goos}" "${goarch}" "${suffix}"
}

kopia_release_archive_name() {
	local goos=$1 goarch=$2
	case "${goos}:${goarch}" in
	linux:amd64) printf 'kopia-%s-linux-x64.tar.gz' "${KOPIA_VERSION}" ;;
	linux:arm64) printf 'kopia-%s-linux-arm64.tar.gz' "${KOPIA_VERSION}" ;;
	darwin:amd64) printf 'kopia-%s-macOS-x64.tar.gz' "${KOPIA_VERSION}" ;;
	darwin:arm64) printf 'kopia-%s-macOS-arm64.tar.gz' "${KOPIA_VERSION}" ;;
	windows:amd64) printf 'kopia-%s-windows-x64.zip' "${KOPIA_VERSION}" ;;
	*) return 2 ;;
	esac
}
