#!/usr/bin/env bash
# Verify host-safe offline Docker package selection and APT planning.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin" "${tmp}/debs"

cat >"${tmp}/bin/dpkg-deb" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
file="${2:-}"
case "${file##*/}" in
docker-ce-cli_*) printf 'docker-ce-cli\n' ;;
docker-ce_*) printf 'docker-ce\n' ;;
docker-compose-plugin_*) printf 'docker-compose-plugin\n' ;;
containerd.io_*) printf 'containerd.io\n' ;;
libnftables1_*) printf 'libnftables1\n' ;;
nftables_*) printf 'nftables\n' ;;
*) exit 2 ;;
esac
SH
cat >"${tmp}/bin/dpkg-query" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
package="${*: -1}"
case "${package}" in
libnftables1|nftables) printf 'ii ' ;;
*) exit 1 ;;
esac
SH
cat >"${tmp}/bin/apt-get" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
	if [[ "${arg}" == "check" ]]; then
		if [[ "${HFL_FAKE_APT_CHECK:-ok}" == "busy" ]]; then
			printf 'E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 42.\n' >&2
			exit 100
		fi
		if [[ "${HFL_FAKE_APT_CHECK:-ok}" == "broken" ]]; then
			printf 'E: Unmet dependencies. Try apt --fix-broken install.\n' >&2
			exit 100
		fi
		exit 0
	fi
done
if [[ "${HFL_FAKE_APT_RESULT:-ok}" == "unresolved" ]]; then
	printf 'docker-ce: Depends: nftables (>= 9.9) but 1.0 is installed\n' >&2
	printf "E: Unmet dependencies. Try 'apt --fix-broken install' with no packages (or specify a solution).\n" >&2
	exit 100
fi
printf '0 upgraded, 4 newly installed, 0 to remove and 0 not upgraded.\n'
SH
cat >"${tmp}/bin/dpkg" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--audit" ]]; then
	if [[ "${HFL_FAKE_DPKG_AUDIT:-ok}" == "broken" ]]; then
		printf 'The following packages have been unpacked but not configured:\n  libpython3.10-stdlib\n' >&2
		printf 'libpython3.10-stdlib\n'
	fi
	exit 0
fi
[[ "${1:-}" == "--install" ]]
printf '%s\n' "${@:2}" >"${HFL_DOCKER_TEST_STATE}/installed"
SH
chmod +x "${tmp}/bin/"*

for package in \
	'docker-ce_5:29.2.1_amd64.deb' \
	'docker-ce-cli_5:29.2.1_amd64.deb' \
	'docker-compose-plugin_5.0.2_amd64.deb' \
	'containerd.io_2.2.1_amd64.deb' \
	'libnftables1_1.1.0_amd64.deb' \
	'nftables_1.1.0_amd64.deb'; do
	: >"${tmp}/debs/${package}"
done

PATH="${tmp}/bin:${PATH}"
export PATH
export HFL_DOCKER_TEST_STATE="${tmp}"
# shellcheck source=../../deploy/bootstrap/gateway-install-docker-ubuntu-amd64.sh
source "${ROOT}/deploy/bootstrap/gateway-install-docker-ubuntu-amd64.sh"

assert_host_apt_healthy

set +e
broken_out="$(HFL_FAKE_APT_CHECK=broken assert_host_apt_healthy 2>&1)"
broken_status=$?
set -e
[[ "${broken_status}" -eq 3 ]]
grep -F 'Host package manager is not healthy enough for offline Docker install.' <<<"${broken_out}" >/dev/null
grep -F 'DANGER: HyperFileLens will not run apt --fix-broken' <<<"${broken_out}" >/dev/null

set +e
dpkg_broken_out="$(HFL_FAKE_DPKG_AUDIT=broken assert_host_apt_healthy 2>&1)"
dpkg_broken_status=$?
set -e
[[ "${dpkg_broken_status}" -eq 3 ]]
grep -F 'Host package manager is not healthy enough for offline Docker install.' <<<"${dpkg_broken_out}" >/dev/null

set +e
busy_out="$(HFL_FAKE_APT_CHECK=busy assert_host_apt_healthy 2>&1)"
busy_status=$?
set -e
[[ "${busy_status}" -eq 3 ]]
grep -F 'The host package manager is busy.' <<<"${busy_out}" >/dev/null
if grep -F 'DANGER: HyperFileLens will not run apt --fix-broken' <<<"${busy_out}" >/dev/null; then
	echo "busy apt state was misclassified as host apt corruption" >&2
	exit 1
fi

select_offline_docker_debs "${tmp}/debs"
[[ "${#deb_files[@]}" -eq 4 ]]
for deb in "${deb_files[@]}"; do
	[[ "${deb##*/}" != *:* ]]
done
[[ -f "${tmp}/debs/docker-ce_5_29.2.1_amd64.deb" ]]
[[ -f "${tmp}/debs/docker-ce-cli_5_29.2.1_amd64.deb" ]]
[[ " $(printf '%s ' "${deb_files[@]##*/}") " != *'nftables_1.1.0_amd64.deb'* ]]
[[ " $(printf '%s ' "${deb_files[@]##*/}") " != *'libnftables1_1.1.0_amd64.deb'* ]]

validate_offline_docker_plan "${tmp}/apt-plan.log" "${deb_files[@]}"
grep -F '0 upgraded, 4 newly installed' "${tmp}/apt-plan.log" >/dev/null
install_offline_docker_debs "${tmp}/install.log" "${deb_files[@]}"
[[ "$(wc -l <"${tmp}/installed")" -eq 4 ]]
grep -F '/docker-ce_5_29.2.1_amd64.deb' "${tmp}/installed" >/dev/null

set +e
unresolved_out="$(
	HFL_FAKE_APT_RESULT=unresolved \
	validate_offline_docker_plan "${tmp}/unsafe-plan.log" "${deb_files[@]}" 2>&1
)"
status=$?
set -e
[[ "${status}" -eq 3 ]]
grep -F 'Docker offline package plan could not be resolved without downloads.' <<<"${unresolved_out}" >/dev/null
if grep -F 'Host package manager is not healthy enough for offline Docker install.' <<<"${unresolved_out}" >/dev/null; then
	echo "offline bundle failure was misclassified as host apt unhealthy" >&2
	exit 1
fi

# If the independent host check fails too, retain the actionable host-APT
# classification even when the simulation emits generic offline dependency text.
set +e
host_broken_out="$(
	HFL_FAKE_APT_RESULT=unresolved HFL_FAKE_APT_CHECK=broken \
	validate_offline_docker_plan "${tmp}/host-broken-plan.log" "${deb_files[@]}" 2>&1
)"
host_broken_status=$?
set -e
[[ "${host_broken_status}" -eq 3 ]]
grep -F 'Host package manager is not healthy enough for offline Docker install.' <<<"${host_broken_out}" >/dev/null

set +e
host_busy_out="$(
	HFL_FAKE_APT_RESULT=unresolved HFL_FAKE_APT_CHECK=busy \
	validate_offline_docker_plan "${tmp}/host-busy-plan.log" "${deb_files[@]}" 2>&1
)"
host_busy_status=$?
set -e
[[ "${host_busy_status}" -eq 3 ]]
grep -F 'The host package manager is busy.' <<<"${host_busy_out}" >/dev/null
if grep -F 'DANGER: HyperFileLens will not run apt --fix-broken' <<<"${host_busy_out}" >/dev/null; then
	echo "busy apt plan was misclassified as host apt corruption" >&2
	exit 1
fi

printf 'Offline Docker package plan checks passed.\n'
