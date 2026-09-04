#!/usr/bin/env bash
# Prepare one canonical Kopia binary matrix for Backend and Agent packaging.
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
# shellcheck source=../lib/logging.sh
source "${SCRIPT_DIR}/../lib/logging.sh"

HFL_LOG_COMPONENT=kopia
export HFL_LOG_COMPONENT

MATRIX="${KOPIA_MATRIX:-${KOPIA_DEFAULT_MATRIX}}"
FORCE=0
OFFLINE=0
PRINT_CONFIG=0
GITHUB_DOWNLOAD_MIRROR="${GITHUB_DOWNLOAD_MIRROR:-}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

die() { hfl_log_fail "$1"; exit "${2:-1}"; }
log() { hfl_log_info "$@"; }
require_value() {
	[[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != - ]] || die "$1 requires a value" 2
}

usage() {
	cat <<'USAGE'
Usage: ./tools/kopia/prepare.sh [options]

  --kopia-mode MODE             build (default) or download
  --kopia-git-url URL           Kopia source repository URL
  --kopia-ref REF               Kopia release ref in vX.Y.Z form
  --matrix MATRIX               Space-separated os:arch entries
  --force                       Ignore a valid cached matrix
  --offline                     Use only the existing source/download caches
  --github-download-mirror URL  Optional mirror for GitHub Git and release downloads
  --github-token TOKEN          Optional GitHub token (environment recommended)
  --print-config                Print resolved non-secret inputs and exit
  -h, --help                    Show this help

Precedence: CLI > environment > tools/kopia/defaults.env
Outputs: build/kopia/dist/<os>/<arch>/kopia[.exe] and build/kopia/KOPIA_INFO.json
USAGE
}

parse_args() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
		-h | --help) usage; exit 0 ;;
		--kopia-mode) require_value "$1" "${2:-}"; KOPIA_ARTIFACT_MODE="$2"; shift 2 ;;
		--kopia-git-url) require_value "$1" "${2:-}"; KOPIA_GIT_URL="$2"; shift 2 ;;
		--kopia-ref) require_value "$1" "${2:-}"; KOPIA_GIT_REF="$2"; shift 2 ;;
		--matrix) require_value "$1" "${2:-}"; MATRIX="$2"; shift 2 ;;
		--force) FORCE=1; shift ;;
		--offline) OFFLINE=1; shift ;;
		--github-download-mirror) require_value "$1" "${2:-}"; GITHUB_DOWNLOAD_MIRROR="$2"; shift 2 ;;
		--github-token) require_value "$1" "${2:-}"; GITHUB_TOKEN="$2"; shift 2 ;;
		--print-config) PRINT_CONFIG=1; shift ;;
		*) die "unknown argument: $1" 2 ;;
		esac
	done
	case "${KOPIA_ARTIFACT_MODE}" in build | download) ;; *) die "invalid --kopia-mode ${KOPIA_ARTIFACT_MODE}" 2 ;; esac
	[[ "${KOPIA_GIT_REF}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || die "invalid --kopia-ref ${KOPIA_GIT_REF} (expected vX.Y.Z)" 2
	KOPIA_VERSION="${BASH_REMATCH[1]}"
	kopia_validate_matrix "${MATRIX}"
}

git_with_auth() {
	local status=0
	if [[ -n "${GITHUB_TOKEN}" ]]; then
		env GIT_TERMINAL_PROMPT=0 git \
			-c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/" "$@" \
			2>&1 | hfl_normalize_native_stream | hfl_log_output_block git \
			|| status="${PIPESTATUS[0]}"
	else
		env GIT_TERMINAL_PROMPT=0 git "$@" \
			2>&1 | hfl_normalize_native_stream | hfl_log_output_block git \
			|| status="${PIPESTATUS[0]}"
	fi
	return "${status}"
}

github_git_mirror_url() {
	local url=$1
	[[ -n "${GITHUB_DOWNLOAD_MIRROR}" && "${url}" == https://github.com/* ]] || return 1
	printf '%s/%s' "${GITHUB_DOWNLOAD_MIRROR%/}" "${url}"
}

git_source_candidates() {
	local url=$1 mirrored
	if mirrored="$(github_git_mirror_url "${url}")"; then
		printf '%s\n' "${mirrored}"
	fi
	printf '%s\n' "${url}"
}

clone_source() {
	local candidate mirrored=""
	mirrored="$(github_git_mirror_url "${KOPIA_GIT_URL}" || true)"
	while IFS= read -r candidate; do
		if [[ -n "${mirrored}" && "${candidate}" == "${mirrored}" ]]; then
			log "Cloning ${KOPIA_GIT_URL} through ${GITHUB_DOWNLOAD_MIRROR%/}"
		else
			[[ -z "${mirrored}" ]] || log "Git mirror unavailable; retrying ${KOPIA_GIT_URL} directly"
			log "Cloning ${KOPIA_GIT_URL}"
		fi
		if git_with_auth clone --no-checkout "${candidate}" "${KOPIA_SOURCE_DIR}"; then
			git -C "${KOPIA_SOURCE_DIR}" remote set-url origin "${KOPIA_GIT_URL}"
			return 0
		fi
		rm -rf "${KOPIA_SOURCE_DIR}"
	done < <(git_source_candidates "${KOPIA_GIT_URL}")
	die "unable to clone Kopia source from the configured mirror or ${KOPIA_GIT_URL}"
}

fetch_source_ref() {
	local candidate mirrored=""
	mirrored="$(github_git_mirror_url "${KOPIA_GIT_URL}" || true)"
	while IFS= read -r candidate; do
		if [[ -n "${mirrored}" && "${candidate}" == "${mirrored}" ]]; then
			log "Fetching ${KOPIA_GIT_REF} through ${GITHUB_DOWNLOAD_MIRROR%/}"
		else
			[[ -z "${mirrored}" ]] || log "Git mirror unavailable; retrying ${KOPIA_GIT_REF} directly"
			log "Fetching ${KOPIA_GIT_REF}"
		fi
		if git_with_auth -C "${KOPIA_SOURCE_DIR}" fetch --force --tags "${candidate}" "${KOPIA_GIT_REF}"; then
			return 0
		fi
	done < <(git_source_candidates "${KOPIA_GIT_URL}")
	die "unable to fetch ${KOPIA_GIT_REF} from the configured mirror or ${KOPIA_GIT_URL}"
}

sync_source() {
	local origin_url mirrored_url=""
	command -v git >/dev/null 2>&1 || die "git is required to prepare Kopia"
	mkdir -p "${KOPIA_BUILD_DIR}"
	if [[ -e "${KOPIA_SOURCE_DIR}" && ! -d "${KOPIA_SOURCE_DIR}/.git" ]]; then
		die "Kopia source cache exists but is not a Git checkout: ${KOPIA_SOURCE_DIR}"
	fi
	if [[ ! -d "${KOPIA_SOURCE_DIR}/.git" ]]; then
		[[ "${OFFLINE}" -eq 0 ]] || die "Kopia source cache is missing and offline mode forbids cloning"
		clone_source
	else
		origin_url="$(git -C "${KOPIA_SOURCE_DIR}" config --get remote.origin.url || true)"
		mirrored_url="$(github_git_mirror_url "${KOPIA_GIT_URL}" || true)"
		if [[ -n "${mirrored_url}" && "${origin_url}" == "${mirrored_url}" ]]; then
			log "Normalizing cached Kopia origin to ${KOPIA_GIT_URL}"
			git -C "${KOPIA_SOURCE_DIR}" remote set-url origin "${KOPIA_GIT_URL}"
		elif [[ "${origin_url}" != "${KOPIA_GIT_URL}" ]]; then
			[[ "${FORCE}" -eq 1 ]] || die "Kopia source cache origin differs from KOPIA_GIT_URL; rerun with --force"
			[[ "${OFFLINE}" -eq 0 ]] || die "offline mode cannot replace the Kopia source cache origin"
			git -C "${KOPIA_SOURCE_DIR}" remote set-url origin "${KOPIA_GIT_URL}"
		fi
	fi
	if [[ "${OFFLINE}" -eq 1 ]]; then
		log "Offline mode: resolving ${KOPIA_GIT_REF} from the source cache"
		git -C "${KOPIA_SOURCE_DIR}" rev-parse --verify "${KOPIA_GIT_REF}^{commit}" >/dev/null \
			|| die "Kopia ref ${KOPIA_GIT_REF} is missing from the offline source cache"
		git -C "${KOPIA_SOURCE_DIR}" reset --hard "${KOPIA_GIT_REF}^{commit}" >/dev/null
	else
		fetch_source_ref
		git -C "${KOPIA_SOURCE_DIR}" reset --hard FETCH_HEAD >/dev/null
	fi
	git -C "${KOPIA_SOURCE_DIR}" clean -fdx >/dev/null
	KOPIA_GIT_COMMIT="$(git -C "${KOPIA_SOURCE_DIR}" rev-parse HEAD)"
}

patch_set_sha256() {
	local patch digest
	for patch in "${KOPIA_PATCH_FILES[@]}"; do
		[[ -f "${patch}" ]] || die "missing Kopia patch: ${patch}"
		digest="$(sha256sum "${patch}" | awk '{print $1}')"
		printf '%s:%s\n' "$(basename "${patch}")" "${digest}"
	done | sha256sum | awk '{print $1}'
}

patch_names() {
	local patch
	for patch in "${KOPIA_PATCH_FILES[@]}"; do
		printf '%s ' "$(basename "${patch}")"
	done
}

patch_digests() {
	local patch
	for patch in "${KOPIA_PATCH_FILES[@]}"; do
		sha256sum "${patch}" | awk '{printf "%s ", $1}'
	done
}

go_toolchain_version() {
	local value
	value="$(awk '$1 == "toolchain" { sub(/^go/, "", $2); print $2; exit }' "${KOPIA_SOURCE_DIR}/go.mod")"
	[[ -n "${value}" ]] || value="$(awk '$1 == "go" { print $2; exit }' "${KOPIA_SOURCE_DIR}/go.mod")"
	[[ -n "${value}" ]] || die "unable to resolve Go version from Kopia go.mod"
	printf '%s' "${value}"
}

cache_is_valid() {
	[[ "${FORCE}" -eq 0 && -f "${KOPIA_INFO_FILE}" ]] || return 1
	MODE="${KOPIA_ARTIFACT_MODE}" URL="${KOPIA_GIT_URL}" REF="${KOPIA_GIT_REF}" \
		COMMIT="${KOPIA_GIT_COMMIT}" PATCH_SHA="${KOPIA_PATCH_SHA256}" GO_VERSION="${KOPIA_GO_VERSION}" \
		BUILD_PROFILE="${KOPIA_BUILD_PROFILE}" MATRIX_VALUE="${MATRIX}" \
		INFO="${KOPIA_INFO_FILE}" ROOT="${KOPIA_BUILD_DIR}" python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

try:
    info = json.loads(Path(os.environ["INFO"]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

expected = {
    "mode": os.environ["MODE"],
    "git_url": os.environ["URL"],
    "git_ref": os.environ["REF"],
    "git_commit": os.environ["COMMIT"],
    "patch_sha256": os.environ["PATCH_SHA"],
    "go_version": os.environ["GO_VERSION"],
    "build_profile": os.environ["BUILD_PROFILE"],
    "matrix": os.environ["MATRIX_VALUE"].split(),
}
if any(info.get(key) != value for key, value in expected.items()):
    raise SystemExit(1)
files = info.get("files")
if not isinstance(files, dict) or set(files) != set(expected["matrix"]):
    raise SystemExit(1)
root = Path(os.environ["ROOT"])
for item in files.values():
    path = root / str(item.get("path", ""))
    if not path.is_file():
        raise SystemExit(1)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item.get("sha256"):
        raise SystemExit(1)
PY
}

build_matrix() {
	local patch
	for patch in "${KOPIA_PATCH_FILES[@]}"; do
		[[ -f "${patch}" ]] || die "missing Kopia patch: ${patch}"
		git -C "${KOPIA_SOURCE_DIR}" apply --check "${patch}" \
			|| die "Kopia patch $(basename "${patch}") does not apply to ${KOPIA_GIT_REF}"
		git -C "${KOPIA_SOURCE_DIR}" apply "${patch}"
	done

	log "Testing the Kopia S3 URL-style patch"
	(
		cd "${KOPIA_SOURCE_DIR}"
		GOTOOLCHAIN="go${KOPIA_GO_VERSION}" go test ./repo/blob/s3 \
			-run 'Test(BucketLookupForURLStyle|OptionsURLStyleJSONRoundTrip|URLStyleRequestAddressing)$'
	)
	log "Testing the Kopia HFL structured-progress patch"
	(
		cd "${KOPIA_SOURCE_DIR}"
		GOTOOLCHAIN="go${KOPIA_GO_VERSION}" go test ./cli -run '^TestHFLStructuredProgress$'
	)
	log "Testing the Kopia HFL entry-summary patch"
	(
		cd "${KOPIA_SOURCE_DIR}"
		GOTOOLCHAIN="go${KOPIA_GO_VERSION}" go test ./cli -run '^TestHFLListSummary$'
	)
	log "Testing the Kopia managed dot-ignore patch"
	(
		cd "${KOPIA_SOURCE_DIR}"
		GOTOOLCHAIN="go${KOPIA_GO_VERSION}" go test ./snapshot/policy ./fs/ignorefs \
			-run '^Test(NoParentDotIgnoreFiles|PolicyMerge)'
	)

	local build_info patch_short entry goos goarch output
	patch_short="${KOPIA_PATCH_SHA256:0:12}"
	build_info="${KOPIA_GIT_COMMIT}+hfl-patch-${patch_short}"
	for entry in ${MATRIX}; do
		IFS=: read -r goos goarch <<<"${entry}"
		output="$(kopia_binary_path "${goos}" "${goarch}")"
		mkdir -p "$(dirname "${output}")"
		log "Building Kopia ${KOPIA_VERSION} for ${goos}/${goarch}"
		(
			cd "${KOPIA_SOURCE_DIR}"
			CGO_ENABLED=0 GOOS="${goos}" GOARCH="${goarch}" GOTOOLCHAIN="go${KOPIA_GO_VERSION}" \
				go build -trimpath -ldflags "-s -w \
				-X github.com/kopia/kopia/repo.BuildVersion=${KOPIA_VERSION} \
				-X github.com/kopia/kopia/repo.BuildInfo=${build_info} \
				-X github.com/kopia/kopia/repo.BuildGitHubRepo=HyperBDR/hyperfilelens" \
				-o "${output}.part" github.com/kopia/kopia
		)
		mv -f "${output}.part" "${output}"
		chmod 755 "${output}"
	done
}

github_repository_path() {
	local url="${KOPIA_GIT_URL%.git}"
	case "${url}" in
	https://github.com/*) printf '%s' "${url#https://github.com/}" ;;
	git@github.com:*) printf '%s' "${url#git@github.com:}" ;;
	*) die "download mode requires a github.com KOPIA_GIT_URL" 2 ;;
	esac
}

download_file() {
	local url=$1 output=$2 candidate part
	local -a candidates curl_args
	part="${output}.part"
	[[ "${OFFLINE}" -eq 0 ]] || return 1
	mkdir -p "$(dirname "${output}")"
	candidates=("${url}")
	if [[ -n "${GITHUB_DOWNLOAD_MIRROR}" ]]; then
		candidates=("${GITHUB_DOWNLOAD_MIRROR%/}/${url}" "${url}")
	fi
	curl_args=(-fsSL --http1.1 --retry 4 --retry-all-errors --retry-delay 2 --connect-timeout 30)
	[[ -z "${GITHUB_TOKEN}" ]] || curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
	for candidate in "${candidates[@]}"; do
		rm -f "${part}"
		if curl "${curl_args[@]}" -o "${part}" "${candidate}"; then
			mv -f "${part}" "${output}"
			return 0
		fi
		rm -f "${part}"
		if command -v wget >/dev/null 2>&1 \
			&& wget --quiet --timeout=30 --tries=3 -O "${part}" "${candidate}"; then
			mv -f "${part}" "${output}"
			return 0
		fi
	done
	rm -f "${part}"
	return 1
}

download_matrix() {
	command -v curl >/dev/null 2>&1 || die "curl is required for Kopia download mode"
	local repo base cache checksums entry goos goarch archive expected actual output
	repo="$(github_repository_path)"
	base="https://github.com/${repo}/releases/download/${KOPIA_GIT_REF}"
	cache="${KOPIA_BUILD_DIR}/download/${KOPIA_GIT_REF}"
	checksums="${cache}/checksums.txt"
	mkdir -p "${cache}"
	if [[ "${FORCE}" -eq 1 || ! -s "${checksums}" ]]; then
		log "Downloading official Kopia checksums"
		download_file "${base}/checksums.txt" "${checksums}" || die "unable to download Kopia checksums"
	fi
	for entry in ${MATRIX}; do
		IFS=: read -r goos goarch <<<"${entry}"
		archive="$(kopia_release_archive_name "${goos}" "${goarch}")"
		expected="$(awk -v name="${archive}" '$2 == name || $2 == "*" name { print $1; exit }' "${checksums}")"
		[[ "${expected}" =~ ^[0-9a-fA-F]{64}$ ]] || die "${archive} is missing from upstream checksums.txt"
		if [[ "${FORCE}" -eq 1 || ! -s "${cache}/${archive}" ]]; then
			log "Downloading official ${archive}"
			download_file "${base}/${archive}" "${cache}/${archive}" || die "unable to download ${archive}"
		fi
		actual="$(sha256sum "${cache}/${archive}" | awk '{print $1}')"
		[[ "${actual}" == "$(printf '%s' "${expected}" | tr '[:upper:]' '[:lower:]')" ]] || die "checksum mismatch for ${archive}"
		output="$(kopia_binary_path "${goos}" "${goarch}")"
		mkdir -p "$(dirname "${output}")"
		ARCHIVE="${cache}/${archive}" OUTPUT="${output}.part" GOOS_VALUE="${goos}" python3 - <<'PY'
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

archive = Path(os.environ["ARCHIVE"])
output = Path(os.environ["OUTPUT"])
want = "kopia.exe" if os.environ["GOOS_VALUE"] == "windows" else "kopia"
if zipfile.is_zipfile(archive):
    with zipfile.ZipFile(archive) as source:
        names = [name for name in source.namelist() if Path(name).name == want]
        if len(names) != 1:
            raise SystemExit(f"expected one {want} in {archive}")
        with source.open(names[0]) as src, output.open("wb") as dst:
            shutil.copyfileobj(src, dst)
else:
    with tarfile.open(archive, "r:gz") as source:
        members = [member for member in source.getmembers() if member.isfile() and Path(member.name).name == want]
        if len(members) != 1:
            raise SystemExit(f"expected one {want} in {archive}")
        src = source.extractfile(members[0])
        if src is None:
            raise SystemExit(f"unable to extract {want} from {archive}")
        with src, output.open("wb") as dst:
            shutil.copyfileobj(src, dst)
PY
		mv -f "${output}.part" "${output}"
		chmod 755 "${output}"
	done
}

write_metadata() {
	MODE="${KOPIA_ARTIFACT_MODE}" URL="${KOPIA_GIT_URL}" REF="${KOPIA_GIT_REF}" \
		COMMIT="${KOPIA_GIT_COMMIT}" VERSION="${KOPIA_VERSION}" GO_VERSION="${KOPIA_GO_VERSION}" \
		PATCH_SHA="${KOPIA_PATCH_SHA256}" BUILD_PROFILE="${KOPIA_BUILD_PROFILE}" \
		PATCH_NAMES="${KOPIA_PATCH_NAMES}" PATCH_DIGESTS="${KOPIA_PATCH_DIGESTS}" \
		MATRIX_VALUE="${MATRIX}" ROOT="${KOPIA_BUILD_DIR}" \
		OUT="${KOPIA_INFO_FILE}" python3 - <<'PY'
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

root = Path(os.environ["ROOT"])
patch_names = os.environ["PATCH_NAMES"].split()
patch_digests = os.environ["PATCH_DIGESTS"].split()
if len(patch_names) != len(patch_digests):
    raise SystemExit("Kopia patch metadata is inconsistent")
patches = [
    {"name": name, "sha256": digest}
    for name, digest in zip(patch_names, patch_digests)
]
files = {}
for entry in os.environ["MATRIX_VALUE"].split():
    goos, goarch = entry.split(":", 1)
    suffix = ".exe" if goos == "windows" else ""
    path = root / "dist" / goos / goarch / f"kopia{suffix}"
    data = path.read_bytes()
    files[entry] = {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }

payload = {
    "schema": 1,
    "mode": os.environ["MODE"],
    "git_url": os.environ["URL"],
    "git_ref": os.environ["REF"],
    "git_commit": os.environ["COMMIT"],
    "version": os.environ["VERSION"],
    "go_version": os.environ["GO_VERSION"],
    "build_profile": os.environ["BUILD_PROFILE"],
    "patch_sha256": os.environ["PATCH_SHA"],
    "patches": patches,
    "matrix": os.environ["MATRIX_VALUE"].split(),
    "features": {
        "s3_url_style": os.environ["MODE"] == "build",
        "hfl_structured_progress_v2": os.environ["MODE"] == "build",
        "hfl_managed_dot_ignore_v1": os.environ["MODE"] == "build",
        "hfl_entry_summary_v1": os.environ["MODE"] == "build",
    },
    "files": files,
    "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
out = Path(os.environ["OUT"])
temporary = out.with_suffix(".json.tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(out)
PY
}

main() {
	kopia_load_config
	parse_args "$@"
	if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
		cat <<EOF
mode=${KOPIA_ARTIFACT_MODE}
git_url=${KOPIA_GIT_URL}
git_ref=${KOPIA_GIT_REF}
version=${KOPIA_VERSION}
matrix=${MATRIX}
output=${KOPIA_BUILD_DIR}
github_download_mirror=${GITHUB_DOWNLOAD_MIRROR:-<official>}
github_token=$([[ -n "${GITHUB_TOKEN}" ]] && printf '<set>' || printf '<unset>')
offline=${OFFLINE}
EOF
		return 0
	fi
	command -v python3 >/dev/null 2>&1 || die "python3 is required to prepare Kopia"
	command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required to prepare Kopia"
	sync_source
	KOPIA_GO_VERSION="$(go_toolchain_version)"
	if [[ "${KOPIA_ARTIFACT_MODE}" == build ]]; then
		KOPIA_PATCH_SHA256="$(patch_set_sha256)"
		KOPIA_PATCH_NAMES="$(patch_names)"
		KOPIA_PATCH_DIGESTS="$(patch_digests)"
		KOPIA_BUILD_PROFILE="cgo-disabled,trimpath,strip,embedded-html-ui,hfl-buildinfo-v2,s3-patch-tests-v1,structured-progress-v2-tests-v1,managed-dot-ignore-v1,entry-summary-v1"
	else
		KOPIA_PATCH_SHA256=""
		KOPIA_PATCH_NAMES=""
		KOPIA_PATCH_DIGESTS=""
		KOPIA_BUILD_PROFILE="official-release-archive"
	fi
	if cache_is_valid; then
		log "Using verified cached Kopia matrix"
		return 0
	fi
	if [[ "${KOPIA_ARTIFACT_MODE}" == build ]]; then
		command -v go >/dev/null 2>&1 || die "Go is required for Kopia build mode"
		build_matrix
	else
		download_matrix
	fi
	write_metadata
	log "Kopia matrix is ready under ${KOPIA_DIST_DIR}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	main "$@"
fi
