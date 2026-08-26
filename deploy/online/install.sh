#!/usr/bin/env bash
# Bootstrap a verified public Community installation or upgrade.
set -euo pipefail

DEFAULT_GLOBAL_REGISTRY_PREFIX="docker.io/oneprolabs"
DEFAULT_CN_REGISTRY_PREFIX="registry.cn-beijing.aliyuncs.com/oneprolabs"
GLOBAL_REGISTRY_PREFIX="${HFL_GLOBAL_REGISTRY_PREFIX:-${DEFAULT_GLOBAL_REGISTRY_PREFIX}}"
CN_REGISTRY_PREFIX="${HFL_CN_REGISTRY_PREFIX:-${DEFAULT_CN_REGISTRY_PREFIX}}"
MIRROR=""
TAG=""
ASSUME_YES=0
SESSION_DIR=""
SOURCE_NAME=""
REGION=""
TAGS_API_URL=""
REGISTRY_NAME=""
RELEASE_VERSION=""
RELEASE_COMMIT=""
RECENT_TAGS=""
INSTALL_ROOT="/opt/hyperfilelens"
INSTALL_ACTION="Install"
MAX_TAG_PAGES=100

usage() {
	cat <<'USAGE'
Usage: install.sh --mirror cn|global [--tag vX.Y.Z] [--yes]

Installs the latest HyperFileLens Community tag on a new host.
Running the command again upgrades an existing Community installation through
the normal managed backup and blue/green lifecycle.

--mirror cn|global     Select Gitee + Alibaba Cloud or GitHub + Docker Hub
--tag vX.Y.Z           Install one specific Community tag
--yes                  Skip the interactive confirmation (automation only)
USAGE
}

fail() {
	printf '[FAIL] %s\n' "$*" >&2
	exit 1
}

cleanup() {
	local rc=$?
	trap - EXIT INT TERM
	if [[ -n "${SESSION_DIR}" && -d "${SESSION_DIR}" ]]; then
		rm -rf -- "${SESSION_DIR}"
	fi
	exit "${rc}"
}

require_value() {
	[[ $# -ge 2 && -n "${2:-}" && "${2:0:1}" != "-" ]] \
		|| fail "$1 requires a value"
}

check_host() {
	[[ "${EUID}" -eq 0 ]] || fail "run this command through sudo"
	[[ -f /etc/os-release ]] || fail "missing /etc/os-release"
	# shellcheck disable=SC1091
	source /etc/os-release
	[[ "${ID:-}" == ubuntu ]] || fail "Ubuntu 20.04, 22.04, or 24.04 is required"
	case "${VERSION_ID:-}" in 20.04 | 22.04 | 24.04) ;; *)
		fail "Ubuntu 20.04, 22.04, or 24.04 is required"
		;; esac
	[[ "$(uname -m)" == x86_64 ]] || fail "linux/amd64 is required"
	command -v curl >/dev/null 2>&1 || fail "curl is required to start the online installer"
	command -v docker >/dev/null 2>&1 || fail "Docker Engine is required"
	docker info >/dev/null 2>&1 || fail "cannot connect to the Docker daemon"
	docker compose version >/dev/null 2>&1 || fail "Docker Compose V2 is required"
}

install_host_tools() {
	local -a missing=()
	local command
	for command in ca-certificates openssl python3 rsync tar; do
		case "${command}" in
		ca-certificates) [[ -f /etc/ssl/certs/ca-certificates.crt ]] || missing+=(ca-certificates) ;;
		*) command -v "${command}" >/dev/null 2>&1 || missing+=("${command}") ;;
		esac
	done
	if ((${#missing[@]})); then
		printf '[....] Installing required host tools: %s\n' "${missing[*]}"
		apt-get update
		DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
	fi
}

configure_mirror() {
	case "${MIRROR}" in
	cn)
		SOURCE_NAME="Gitee"
		REGION="cn"
		TAGS_API_URL="https://gitee.com/api/v5/repos/oneprolabs/hyperfilelens/tags?per_page=100&page=1"
		REGISTRY_NAME="Alibaba Cloud"
		;;
	global)
		SOURCE_NAME="GitHub"
		REGION="global"
		TAGS_API_URL="https://api.github.com/repos/oneprolabs/hyperfilelens/tags?per_page=100&page=1"
		REGISTRY_NAME="Docker Hub"
		;;
	*) fail "--mirror must be cn or global" ;;
	esac
}

inspect_existing_installation() {
	local existing_edition
	if [[ ! -e "${INSTALL_ROOT}/.env" && ! -e "${INSTALL_ROOT}/MANIFEST.json" ]]; then
		return 0
	fi
	INSTALL_ACTION="Upgrade"
	[[ -f "${INSTALL_ROOT}/.env" && -f "${INSTALL_ROOT}/MANIFEST.json" ]] \
		|| fail "${INSTALL_ROOT} contains an incomplete installation; recover or remove it before continuing"
	existing_edition="$(python3 - "${INSTALL_ROOT}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

try:
    manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
print(str(manifest.get("edition") or "community").strip().lower())
PY
	)" || fail "the existing installation manifest is invalid"
	[[ "${existing_edition}" == community ]] \
		|| fail "this public installer upgrades Community only; the existing edition is ${existing_edition}"
}

download_file() {
	local url=$1
	local output=$2
	local max_time=${3:-120}
	curl --fail --show-error --silent --location \
		--retry 3 --retry-all-errors --connect-timeout 15 --max-time "${max_time}" \
		-H 'Cache-Control: no-cache' "${url}" -o "${output}"
}

fail_with_tag_guidance() {
	local reason=$1
	local recommended_tag
	if [[ -z "${RECENT_TAGS}" ]]; then
		fail "${reason}; no fallback tags are available; retry later or use --mirror cn or --mirror global"
	fi
	recommended_tag="${RECENT_TAGS%%,*}"
	fail "${reason}; recent fallback tags: ${RECENT_TAGS}; recommended retry: --mirror ${MIRROR} --tag ${recommended_tag}"
}

tag_page_url() {
	local page=$1
	printf '%s' "${TAGS_API_URL%page=1}page=${page}"
}

resolve_tag() {
	local parsed requested_tag page count page_url page_fingerprint
	local -a values=()
	local -A seen_page_fingerprints=()
	requested_tag="${TAG}"
	printf '[....] Resolving Community tags from %s\n' "${SOURCE_NAME}"
	page=1
	while :; do
		page_url="$(tag_page_url "${page}")"
		if ! download_file "${page_url}" "${SESSION_DIR}/tag-page-${page}.json"; then
			fail "could not read Community tags from ${SOURCE_NAME}; retry later or use --mirror cn or --mirror global"
		fi
		if ! read -r count page_fingerprint < <(python3 - "${SESSION_DIR}/tag-page-${page}.json" <<'PY'
import hashlib
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    raw = path.read_bytes()
    payload = json.loads(raw)
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(payload, list):
    raise SystemExit(1)
print(len(payload), hashlib.sha256(raw).hexdigest())
PY
		); then
			fail "the Community tag response from ${SOURCE_NAME} is invalid"
		fi
		if [[ -n "${seen_page_fingerprints[${page_fingerprint}]:-}" ]]; then
			fail "the Community tag response from ${SOURCE_NAME} repeated page ${page}; retry later"
		fi
		seen_page_fingerprints["${page_fingerprint}"]=1
		if ((page == 1)); then
			cp "${SESSION_DIR}/tag-page-${page}.json" "${SESSION_DIR}/tags.json"
		else
			if ! python3 - "${SESSION_DIR}/tags.json" "${SESSION_DIR}/tag-page-${page}.json" <<'PY'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
current = json.loads(target.read_text(encoding="utf-8"))
additional = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
if not isinstance(current, list) or not isinstance(additional, list):
    raise SystemExit(1)
target.write_text(json.dumps(current + additional), encoding="utf-8")
PY
			then
				fail "the Community tag response from ${SOURCE_NAME} is invalid"
			fi
		fi
		((count < 100)) && break
		if ((page >= MAX_TAG_PAGES)); then
			fail "the Community tag catalog from ${SOURCE_NAME} exceeds ${MAX_TAG_PAGES} pages"
		fi
		page=$((page + 1))
	done

	if ! parsed="$(python3 - "${SESSION_DIR}/tags.json" "${requested_tag}" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
requested = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid tag response: {exc}")
if not isinstance(payload, list):
    raise SystemExit("tag response is not a list")

tags = {}
for entry in payload:
    if not isinstance(entry, dict):
        continue
    tag = str(entry.get("name") or "")
    commit_info = entry.get("commit")
    commit = str(commit_info.get("sha") or "").lower() if isinstance(commit_info, dict) else ""
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        continue
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        continue
    if tag in tags and tags[tag] != commit:
        raise SystemExit(f"tag {tag} resolves to multiple commits")
    tags[tag] = commit

ordered = sorted(
    tags,
    key=lambda value: tuple(int(part) for part in value[1:].split(".")),
    reverse=True,
)
if not ordered:
    raise SystemExit("tag response contains no semantic release tags")

selected = requested if requested in tags else (ordered[0] if not requested else "")
if selected:
    selected_version = tuple(int(part) for part in selected[1:].split("."))
    fallback = [
        tag
        for tag in ordered
        if tuple(int(part) for part in tag[1:].split(".")) < selected_version
    ][:10]
else:
    fallback = ordered[:10]

print(selected or "-")
print(selected[1:] if selected else "-")
print(tags.get(selected, "-") if selected else "-")
print(", ".join(fallback) or "-")
PY
	)"; then
		fail "the Community tag response from ${SOURCE_NAME} is invalid"
	fi
	mapfile -t values <<<"${parsed}"
	[[ "${#values[@]}" -eq 4 ]] || fail "the Community tag response from ${SOURCE_NAME} is incomplete"
	RECENT_TAGS="${values[3]}"
	[[ "${RECENT_TAGS}" != - ]] || RECENT_TAGS=""
	if [[ -n "${requested_tag}" && "${values[0]}" == - ]]; then
		fail_with_tag_guidance "Community tag ${requested_tag} does not exist on ${SOURCE_NAME}"
	fi
	TAG="${values[0]}"
	RELEASE_VERSION="${values[1]}"
	RELEASE_COMMIT="${values[2]}"
}

confirm_installation() {
	local answer=""
	printf '\nSelected tag: %s\n' "${TAG}"
	printf 'Download source: %s\n' "${SOURCE_NAME}"
	printf 'Image registry: %s\n' "${REGISTRY_NAME}"
	printf 'Install directory: %s\n' "${INSTALL_ROOT}"
	printf 'Action: %s\n\n' "${INSTALL_ACTION}"
	((ASSUME_YES == 1)) && return 0
	[[ -r /dev/tty ]] \
		|| fail "interactive confirmation requires a terminal; use --yes only for automation"
	read -r -p 'Continue? [y/N] ' answer </dev/tty
	case "${answer}" in y | Y | yes | YES | Yes) ;; *) fail "installation cancelled" ;; esac
}

download_source_archive() {
	local url
	case "${MIRROR}" in
	global) url="https://codeload.github.com/oneprolabs/hyperfilelens/tar.gz/${RELEASE_COMMIT}" ;;
	cn) url="https://gitee.com/oneprolabs/hyperfilelens/repository/archive/${RELEASE_COMMIT}.tar.gz" ;;
	esac
	printf '[....] Downloading %s installation contract from %s (commit %s)\n' \
		"${TAG}" "${SOURCE_NAME}" "${RELEASE_COMMIT:0:12}"
	if ! download_file "${url}" "${SESSION_DIR}/source.tar.gz" 300; then
		fail_with_tag_guidance "Community release ${TAG} cannot be downloaded from ${SOURCE_NAME}"
	fi
	mkdir -p "${SESSION_DIR}/source"
	tar -xzf "${SESSION_DIR}/source.tar.gz" -C "${SESSION_DIR}/source" --strip-components=1 \
		|| fail_with_tag_guidance "Community tag ${TAG} installation contract could not be extracted"
	[[ -x "${SESSION_DIR}/source/deploy/online/install.sh" \
		&& -f "${SESSION_DIR}/source/deploy/online/prepare.py" ]] \
		|| fail_with_tag_guidance "Community tag ${TAG} does not provide the online installation contract"
	printf '[ OK ] Downloaded installation contract from %s\n' "${SOURCE_NAME}"
}

verify_candidate_release() {
	python3 - "${candidate}/MANIFEST.json" "${TAG}" "${RELEASE_COMMIT}" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
expected_tag = sys.argv[2]
expected_commit = sys.argv[3]
try:
    manifest = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"prepared Community manifest is invalid: {exc}")
version = str(manifest.get("version") or "")
commit = str(manifest.get("git_commit") or "").lower()
if expected_tag != f"v{version}":
    raise SystemExit("prepared Community version does not match the published release")
if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit != expected_commit:
    raise SystemExit("prepared Community image revision does not match the published release")
if manifest.get("edition") != "community" or manifest.get("channel") != "release":
    raise SystemExit("prepared package is not a Community release")
PY
}

while (($#)); do
	case "$1" in
	--mirror)
		require_value "$@"
		MIRROR=$2
		shift 2
		;;
	--tag)
		require_value "$@"
		[[ -z "${TAG}" ]] || fail "--tag may only be supplied once"
		TAG=$2
		shift 2
		;;
	--yes)
		ASSUME_YES=1
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*) fail "unknown argument: $1" ;;
	esac
done

[[ -z "${TAG}" || "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
	|| fail "--tag must use vX.Y.Z; choose a published version with --mirror cn|global --tag vX.Y.Z"
configure_mirror
check_host

SESSION_DIR="$(mktemp -d /var/tmp/hyperfilelens-online.XXXXXX)"
chmod 0700 "${SESSION_DIR}"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

install_host_tools
inspect_existing_installation
resolve_tag
confirm_installation
download_source_archive

export HFL_GLOBAL_REGISTRY_PREFIX="${GLOBAL_REGISTRY_PREFIX}"
export HFL_CN_REGISTRY_PREFIX="${CN_REGISTRY_PREFIX}"
export HFL_REGISTRY_REGION="${REGION}"
candidate="${SESSION_DIR}/hyperfilelens-${RELEASE_VERSION}-online"
if ! python3 "${SESSION_DIR}/source/deploy/online/prepare.py" \
	--source-root "${SESSION_DIR}/source" \
	--version "${TAG}" \
	--region "${REGION}" \
	--output "${candidate}"; then
	fail_with_tag_guidance "Community tag ${TAG} is incomplete or unavailable"
fi
if ! verify_candidate_release; then
	fail_with_tag_guidance "Community tag ${TAG} failed release identity validation"
fi

if [[ "${INSTALL_ACTION}" == Upgrade ]]; then
	printf '[....] Upgrading the existing installation to %s\n' "${TAG}"
	HFL_REGISTRY_REGION="${REGION}" bash "${candidate}/install.sh" \
		upgrade --from "${candidate}" --yes --with-sourcelens
else
	printf '[....] Installing HyperFileLens Community %s\n' "${TAG}"
	HFL_REGISTRY_REGION="${REGION}" bash "${candidate}/install.sh" \
		install --with-sourcelens --yes
fi
