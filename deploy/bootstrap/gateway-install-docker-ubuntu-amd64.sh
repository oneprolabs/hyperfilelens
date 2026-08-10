#!/usr/bin/env bash
# Offline Docker CE install for HyperFileLens Data Gateways (Ubuntu 20.04/22.04/24.04 amd64).
# Bundled under /media/gateway-bootstrap/ and invoked by hfl-enroll gateway-install
# after full preflight and Agent registration (AI engine setup).
#
# Host-safe policy: only install packages from the bundled .deb archive. Never run
# apt-get --fix-broken or other apt operations that can remove or upgrade unrelated
# host packages (python, cloud-init, nfs-common, etc.) on customer-provided machines.
set -euo pipefail

HFL_GATEWAY_BOOTSTRAP_BASE="${HFL_GATEWAY_BOOTSTRAP_BASE:-${HFL_API_BASE:-}/media/gateway-bootstrap}"
DOCKER_DEBS_ARCHIVE=""
MIN_ENGINE_VERSION="${HFL_DOCKER_MIN_ENGINE:-24.0.0}"
MIN_COMPOSE_VERSION="${HFL_COMPOSE_MIN_VERSION:-2.20.0}"

hfl_now() {
	date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ
}

hfl_step() {
	printf '[%s] [....] %s\n' "$(hfl_now)" "$1"
}

hfl_ok() {
	printf '[%s] [ OK  ] %s\n' "$(hfl_now)" "$1"
}

hfl_warn() {
	printf '[%s] [WARN ] %s\n' "$(hfl_now)" "$1" >&2
}

hfl_fail() {
	printf '[%s] [FAIL ] %s\n' "$(hfl_now)" "$1" >&2
	exit "${2:-1}"
}

hfl_format_bytes() {
	awk -v bytes="$1" 'BEGIN {
		split("B KiB MiB GiB TiB", units, " ")
		value = bytes + 0
		unit = 1
		while (value >= 1024 && unit < 5) {
			value /= 1024
			unit++
		}
		if (unit == 1) printf "%.0f %s", value, units[unit]
		else printf "%.1f %s", value, units[unit]
	}'
}

hfl_download() {
	local label="$1"
	local url="$2"
	local destination="$3"
	local partial="${destination}.part"
	local started=${SECONDS} elapsed bytes rate
	local -a retry_connrefused=()
	rm -f "${partial}"
	hfl_step "Downloading ${label}."
	# Older system curl builds may predate --retry-connrefused.
	if curl --retry-connrefused --version >/dev/null 2>&1; then
		retry_connrefused=(--retry-connrefused)
	fi
	# ${arr[@]+...} keeps empty CURL_TLS safe under `set -u` on Bash < 4.4.
	if ! curl ${CURL_TLS[@]+"${CURL_TLS[@]}"} \
		--fail --show-error --location --progress-bar \
		--retry 3 ${retry_connrefused[@]+"${retry_connrefused[@]}"} --retry-delay 2 \
		"${url}" -o "${partial}"; then
		rm -f "${partial}"
		hfl_fail "Failed to download ${label}." 3
	fi
	mv -f "${partial}" "${destination}"
	bytes="$(wc -c <"${destination}")"
	elapsed=$((SECONDS - started))
	((elapsed > 0)) || elapsed=1
	rate="$(hfl_format_bytes "$((bytes / elapsed))")/s"
	hfl_ok "${label} downloaded ($(hfl_format_bytes "${bytes}") in ${elapsed}s, average ${rate})."
}

CURL_TLS=(-k)
if [[ "${HFL_INSECURE_TLS:-1}" == "0" ]]; then
	CURL_TLS=()
fi

docker_engine_version() {
	docker version --format '{{.Server.Version}}' 2>/dev/null || true
}

docker_compose_version() {
	if docker compose version >/dev/null 2>&1; then
		docker compose version --short 2>/dev/null || docker compose version 2>/dev/null | awk '{print $NF}'
		return 0
	fi
	printf ''
}

docker_version_ge() {
	local have="$1"
	local want="$2"
	[[ -n "${have}" && -n "${want}" ]] || return 1
	dpkg --compare-versions "${have#v}" ge "${want#v}"
}

docker_runtime_ready() {
	local min_version="${1:-24.0.0}"
	command -v docker >/dev/null 2>&1 || return 1
	docker info >/dev/null 2>&1 || return 1
	local engine compose
	engine="$(docker_engine_version)"
	[[ -n "${engine}" ]] || return 1
	docker_version_ge "${engine}" "${min_version}" || return 1
	compose="$(docker_compose_version)"
	[[ -n "${compose}" ]] || return 1
	docker_version_ge "${compose}" "${MIN_COMPOSE_VERSION}" || return 1
	return 0
}

assert_host_os() {
	local id version_id arch
	if [[ -r /etc/os-release ]]; then
		# shellcheck disable=SC1091
		. /etc/os-release
	fi
	arch="$(uname -m)"
	[[ "${ID:-}" == "ubuntu" ]] \
		|| hfl_fail "Docker offline install requires Ubuntu (current: ${ID:-unknown} ${VERSION_ID:-unknown})." 4
	case "${VERSION_ID:-}" in
	20.04) DOCKER_DEBS_ARCHIVE="docker-debs-ubuntu2004-amd64.tar.gz" ;;
	22.04) DOCKER_DEBS_ARCHIVE="docker-debs-ubuntu2204-amd64.tar.gz" ;;
	24.04) DOCKER_DEBS_ARCHIVE="docker-debs-ubuntu2404-amd64.tar.gz" ;;
	*) hfl_fail "Docker offline install supports Ubuntu 20.04, 22.04, or 24.04 (current: ${VERSION_ID:-unknown})." 4 ;;
	esac
	[[ "${arch}" == "x86_64" || "${arch}" == "amd64" ]] \
		|| hfl_fail "Docker offline install requires amd64 (current: ${arch})." 4
}

run_quiet() {
	local log_file="$1"
	shift
	if [[ "${HFL_VERBOSE:-0}" == "1" ]]; then
		"$@"
	else
		"$@" >"${log_file}" 2>&1
	fi
}

select_offline_docker_debs() {
	local root="$1"
	local deb package status filename normalized
	local -a bundled_debs=()
	deb_files=()

	mapfile -d '' -t bundled_debs < <(find "${root}" -name '*.deb' -type f -print0 | sort -z)
	for deb in "${bundled_debs[@]}"; do
		# APT treats the epoch separator in an absolute local filename as a URI
		# delimiter and can silently ignore that package. Verify the archive before
		# this function, then give every deb an APT-safe local filename.
		filename="${deb##*/}"
		normalized="${deb%/*}/${filename//:/_}"
		if [[ "${normalized}" != "${deb}" ]]; then
			[[ ! -e "${normalized}" ]] \
				|| hfl_fail "Docker offline package filename collision: ${normalized##*/}" 3
			mv -- "${deb}" "${normalized}"
			deb="${normalized}"
		fi

		package="$(dpkg-deb --field "${deb}" Package 2>/dev/null || true)"
		[[ "${package}" =~ ^[a-z0-9][a-z0-9+.-]+$ ]] \
			|| hfl_fail "Docker offline bundle contains an invalid deb: ${filename}" 3
		status="$(dpkg-query -W -f='${db:Status-Abbrev}' -- "${package}" 2>/dev/null || true)"
		if [[ "${status}" == "ii " ]]; then
			hfl_ok "Using installed host dependency ${package}."
			continue
		fi
		deb_files+=("${deb}")
	done

	((${#deb_files[@]} > 0)) \
		|| hfl_fail "Docker offline bundle has no packages left to install." 3
}

validate_offline_docker_plan() {
	local apt_plan="$1"
	shift
	if ! apt-get --simulate --no-download --no-install-recommends install "$@" \
		>"${apt_plan}" 2>&1; then
		tail -n 12 "${apt_plan}" >&2 || true
		hfl_fail "Docker offline package plan could not be resolved without downloads." 3
	fi
	if grep -Eq '^The following packages will be (REMOVED|upgraded):|^[1-9][0-9]* upgraded,' "${apt_plan}"; then
		cat "${apt_plan}" >&2
		hfl_fail "Docker offline install would upgrade or remove existing host packages." 3
	fi
}

install_offline_docker_debs() {
	local install_log="$1"
	shift
	# APT has already proved that the exact local package set is dependency-safe
	# and requires no upgrades, removals, or downloads. Install those verified
	# files directly: apt-get --no-download can lose the directory of local debs
	# and fail with "Pathname to install is not absolute" on Ubuntu 24.04.
	if ! run_quiet "${install_log}" env DEBIAN_FRONTEND=noninteractive dpkg --install "$@"; then
		[[ -s "${install_log}" ]] && tail -n 12 "${install_log}" >&2
		hfl_fail "Docker offline package installation failed." 3
	fi
}

main() {
if [[ "$(id -u)" -ne 0 ]]; then
	hfl_fail "Administrator privileges are required." 1
fi

if docker_runtime_ready "${MIN_ENGINE_VERSION}"; then
	hfl_ok "Using existing Docker (engine $(docker_engine_version), compose $(docker_compose_version))."
	exit 0
fi

if command -v docker >/dev/null 2>&1; then
	hfl_fail "Docker is installed but does not meet the requirements (engine >= ${MIN_ENGINE_VERSION}, Compose v2 >= ${MIN_COMPOSE_VERSION}, reachable daemon). Fix Docker without using the HFL installer." 3
fi

assert_host_os

if dpkg-query -W -f='${db:Status-Abbrev}\n' \
	docker-ce docker-ce-cli docker-compose-plugin containerd.io 2>/dev/null \
	| grep -q '^ii '; then
	hfl_fail "Docker packages are partially installed even though the docker command is unavailable. Repair or remove the existing Docker installation first." 3
fi
if [[ -e /var/lib/docker || -e /run/docker.sock ]]; then
	hfl_fail "Docker state exists even though the docker command is unavailable. Refusing to overwrite a partial installation." 3
fi

if command -v dpkg >/dev/null 2>&1; then
	audit_out="$(dpkg --audit 2>/dev/null || true)"
	if [[ -n "${audit_out}" ]]; then
		hfl_warn "dpkg reports broken packages on this host:"
		printf '%s\n' "${audit_out}" | sed 's/^/  /' >&2
		hfl_fail "Repair the host package state before installing Docker." 3
	fi
fi

if ! command -v curl >/dev/null 2>&1; then
	hfl_fail "curl is required to download Docker offline packages." 2
fi

base="${HFL_GATEWAY_BOOTSTRAP_BASE%/}"
if [[ -z "${base}" || "${base}" == "/media/gateway-bootstrap" ]]; then
	hfl_fail "HFL_GATEWAY_BOOTSTRAP_BASE or HFL_API_BASE must be set." 2
fi

work_dir="$(mktemp -d /tmp/hfl-docker-debs-XXXXXX)"
cleanup() { rm -rf "${work_dir}"; }
trap cleanup EXIT

archive_url="${base}/${DOCKER_DEBS_ARCHIVE}"
hfl_download \
	"Docker CE offline bundle" \
	"${archive_url}" \
	"${work_dir}/${DOCKER_DEBS_ARCHIVE}"

tar -xzf "${work_dir}/${DOCKER_DEBS_ARCHIVE}" -C "${work_dir}"
deb_count="$(find "${work_dir}" -maxdepth 2 -name '*.deb' | wc -l | tr -d ' ')"
if [[ "${deb_count}" -lt 5 ]]; then
	hfl_fail "Docker offline bundle is incomplete (${deb_count} debs)." 3
fi

python3 - "${work_dir}" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
manifest_path = root / "MANIFEST.json"
if not manifest_path.is_file():
    raise SystemExit("Docker offline bundle has no MANIFEST.json")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
for package in manifest.get("packages", []):
    path = root / package["file"]
    if not path.is_file():
        raise SystemExit(f"Docker offline bundle is missing {path.name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != package.get("sha256"):
        raise SystemExit(f"Docker offline package checksum mismatch: {path.name}")
PY

hfl_step "Installing Docker CE from offline packages (${deb_count} debs)."
command -v apt-get >/dev/null 2>&1 \
	|| hfl_fail "apt-get is required for the verified offline Docker install." 2
command -v dpkg-deb >/dev/null 2>&1 \
	|| hfl_fail "dpkg-deb is required for the verified offline Docker install." 2
select_offline_docker_debs "${work_dir}"
hfl_step "Selected ${#deb_files[@]} new packages; installed host dependencies will remain unchanged."
apt_plan="${work_dir}/apt-plan.log"
validate_offline_docker_plan "${apt_plan}" "${deb_files[@]}"
apt_log="${work_dir}/apt-install.log"
install_offline_docker_debs "${apt_log}" "${deb_files[@]}"

if command -v systemctl >/dev/null 2>&1; then
	systemctl enable --now docker >/dev/null 2>&1 \
		|| hfl_fail "Docker was installed but its service could not be enabled and started." 5
	sleep 2
fi

docker_runtime_ready "${MIN_ENGINE_VERSION}" \
	|| hfl_fail "Docker post-install check failed (need engine >= ${MIN_ENGINE_VERSION} and Compose v2 >= ${MIN_COMPOSE_VERSION})." 5

hfl_ok "Docker CE installed (engine $(docker_engine_version), compose $(docker_compose_version))."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	main "$@"
fi
