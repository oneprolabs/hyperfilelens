#!/usr/bin/env bash
# Bootstrap a public Community installation or upgrade from one HFL Git tag.
set -euo pipefail

GLOBAL_REGISTRY_PREFIX="${HFL_GLOBAL_REGISTRY_PREFIX:-docker.io/oneprolabs}"
CN_REGISTRY_PREFIX="${HFL_CN_REGISTRY_PREFIX:-registry.cn-beijing.aliyuncs.com/oneprolabs}"
REGION="${HFL_REGISTRY_REGION:-auto}"
DOWNLOAD_SOURCE="${HFL_DOWNLOAD_SOURCE:-auto}"
ASSUME_YES=0
TAG=""
SESSION_DIR=""

usage() {
	cat <<'USAGE'
Usage: install.sh vX.Y.Z [--region auto|cn|global] [--download-source auto|github|gitee] [--yes]

Installs HyperFileLens Community on a new host. Running the command again with
a newer tag performs the normal managed backup and blue/green upgrade.

--region selects the preferred public image registry. --download-source selects
the GitHub or Gitee source archive; auto uses Gitee first in China and GitHub
first elsewhere, with the other source as a fallback. --yes enables
non-interactive installation and upgrade.
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

detect_region() {
	local timezone=""
	timezone="$(cat /etc/timezone 2>/dev/null || true)"
	case "${timezone}" in
	Asia/Shanghai | Asia/Chongqing | Asia/Harbin | Asia/Urumqi) printf 'cn' ;;
	*) printf 'global' ;;
	esac
}

ensure_prerequisites() {
	[[ "${EUID}" -eq 0 ]] || fail "run this command through sudo"
	[[ -f /etc/os-release ]] || fail "missing /etc/os-release"
	# shellcheck disable=SC1091
	source /etc/os-release
	[[ "${ID:-}" == ubuntu ]] || fail "Ubuntu 20.04, 22.04, or 24.04 is required"
	case "${VERSION_ID:-}" in 20.04 | 22.04 | 24.04) ;; *)
		fail "Ubuntu 20.04, 22.04, or 24.04 is required"
		;; esac
	[[ "$(uname -m)" == x86_64 ]] || fail "linux/amd64 is required"

	local -a missing=()
	local command
	for command in ca-certificates curl openssl python3 rsync tar; do
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
	command -v docker >/dev/null 2>&1 || fail "Docker Engine is required"
	docker info >/dev/null 2>&1 || fail "cannot connect to the Docker daemon"
	docker compose version >/dev/null 2>&1 || fail "Docker Compose V2 is required"
}

while (($#)); do
	case "$1" in
	--region)
		[[ $# -ge 2 ]] || fail "--region requires auto, cn, or global"
		REGION=$2
		shift 2
		;;
	--download-source)
		[[ $# -ge 2 ]] || fail "--download-source requires auto, github, or gitee"
		DOWNLOAD_SOURCE=$2
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
	v[0-9]*.[0-9]*.[0-9]*)
		[[ -z "${TAG}" ]] || fail "only one HFL tag may be supplied"
		TAG=$1
		shift
		;;
	*) fail "unknown argument: $1" ;;
	esac
done

[[ "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
	usage >&2
	exit 2
}
case "${REGION}" in
auto) REGION="$(detect_region)" ;;
cn | global) ;;
*) fail "--region must be auto, cn, or global" ;;
esac
case "${DOWNLOAD_SOURCE}" in
auto | github | gitee) ;;
*) fail "--download-source must be auto, github, or gitee" ;;
esac

ensure_prerequisites
export HFL_GLOBAL_REGISTRY_PREFIX="${GLOBAL_REGISTRY_PREFIX}"
export HFL_CN_REGISTRY_PREFIX="${CN_REGISTRY_PREFIX}"
export HFL_REGISTRY_REGION="${REGION}"

SESSION_DIR="$(mktemp -d /var/tmp/hyperfilelens-online.XXXXXX)"
chmod 0700 "${SESSION_DIR}"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
archive="${SESSION_DIR}/source.tar.gz"
source_dir="${SESSION_DIR}/source"
candidate="${SESSION_DIR}/hyperfilelens-${TAG#v}-online"

download_source_archive() {
	local source url
	local -a sources=()
	case "${DOWNLOAD_SOURCE}" in
	auto)
		if [[ "${REGION}" == cn ]]; then
			sources=(gitee github)
		else
			sources=(github gitee)
		fi
		;;
	github | gitee) sources=("${DOWNLOAD_SOURCE}") ;;
	esac

	for source in "${sources[@]}"; do
		case "${source}" in
		github)
			url="https://codeload.github.com/oneprolabs/hyperfilelens/tar.gz/refs/tags/${TAG}"
			;;
		gitee)
			url="https://gitee.com/oneprolabs/hyperfilelens/repository/archive/${TAG}.tar.gz"
			;;
		esac
		printf '[....] Downloading %s installation contract from %s\n' "${TAG}" "${source}"
		rm -rf -- "${source_dir}"
		rm -f -- "${archive}"
		if ! curl --fail --show-error --location --retry 3 --retry-all-errors \
			--connect-timeout 15 --max-time 300 "${url}" -o "${archive}"; then
			printf '[WARN] %s installation contract download failed; trying the next source\n' \
				"${source}" >&2
			continue
		fi
		mkdir -p "${source_dir}"
		if ! tar -xzf "${archive}" -C "${source_dir}" --strip-components=1 \
			|| [[ ! -x "${source_dir}/deploy/online/install.sh" ]] \
			|| [[ ! -f "${source_dir}/deploy/online/prepare.py" ]]; then
			printf '[WARN] %s archive does not provide the online installation contract; trying the next source\n' \
				"${source}" >&2
			continue
		fi
		printf '[ OK ] Downloaded installation contract from %s\n' "${source}"
		return 0
	done
	fail "could not download ${TAG} installation contract from the selected source(s)"
}

download_source_archive

python3 "${source_dir}/deploy/online/prepare.py" \
	--source-root "${source_dir}" \
	--version "${TAG}" \
	--region "${REGION}" \
	--output "${candidate}"

install_root=/opt/hyperfilelens
if [[ -e "${install_root}/.env" || -e "${install_root}/MANIFEST.json" ]]; then
	[[ -f "${install_root}/.env" && -f "${install_root}/MANIFEST.json" ]] \
		|| fail "${install_root} contains an incomplete installation; recover or remove it before continuing"
	existing_edition="$(python3 - "${install_root}/MANIFEST.json" <<'PY'
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
	printf '[....] Upgrading the existing installation to %s\n' "${TAG}"
	HFL_REGISTRY_REGION="${REGION}" bash "${candidate}/install.sh" \
		upgrade --from "${candidate}" --yes --with-sourcelens
else
	printf '[....] Installing HyperFileLens Community %s\n' "${TAG}"
	install_args=(install --with-sourcelens)
	((ASSUME_YES == 1)) && install_args+=(--yes)
	HFL_REGISTRY_REGION="${REGION}" bash "${candidate}/install.sh" "${install_args[@]}"
fi
