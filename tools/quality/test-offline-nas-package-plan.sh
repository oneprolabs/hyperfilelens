#!/usr/bin/env bash
# Verify that offline NAS dependency installation preserves healthy host packages.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
installer="${ROOT}/src/agent/packaging/install/install.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin" "${tmp}/debs"

# Load helpers without dispatching the real installer.
# shellcheck disable=SC1090
source <(sed '/^case "$CMD" in/,$d' "${installer}")

cat >"${tmp}/bin/dpkg-deb" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${3:-}" in
Package)
	case "${2##*/}" in
	python3_*) printf 'python3\n' ;;
	nfs-common_*) printf 'nfs-common\n' ;;
	cifs-utils_*) printf 'cifs-utils\n' ;;
	rpcbind_*) printf 'rpcbind\n' ;;
	broken_*) printf 'broken\n' ;;
	*) exit 2 ;;
	esac
	;;
Architecture) printf 'amd64\n' ;;
*) exit 2 ;;
esac
SH
cat >"${tmp}/bin/dpkg-query" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${*: -1}" in
python3:amd64) printf 'hi ' ;;
nfs-common:amd64) printf 'un ' ;;
rpcbind:amd64) printf 'rc ' ;;
broken:amd64) printf 'iU ' ;;
*) exit 1 ;;
esac
SH
cat >"${tmp}/bin/apt-get" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >"${HFL_NAS_TEST_STATE}/apt-args"
sources=""
source_parts=""
previous=""
for argument in "$@"; do
	case "${previous}" in
	Dir::Etc::sourcelist) sources="${argument}" ;;
	Dir::Etc::sourceparts) source_parts="${argument}" ;;
	esac
	case "${argument}" in
	Dir::Etc::sourcelist=*) sources="${argument#*=}" ;;
	Dir::Etc::sourceparts=*) source_parts="${argument#*=}" ;;
	esac
	previous="${argument}"
done
[[ -f "${sources}" && ! -s "${sources}" ]]
[[ -d "${source_parts}" ]]
case "${HFL_TEST_APT_RESULT:-safe}" in
upgrade)
	printf 'The following packages will be upgraded:\n  python3\n'
	printf '1 upgraded, 3 newly installed, 0 to remove and 0 not upgraded.\n'
	;;
downgrade)
	printf 'The following packages will be DOWNGRADED:\n  python3\n'
	printf '0 upgraded, 3 newly installed, 1 downgraded, 0 to remove and 0 not upgraded.\n'
	;;
remove)
	printf 'The following packages will be REMOVED:\n  python3\n'
	printf '0 upgraded, 3 newly installed, 1 to remove and 0 not upgraded.\n'
	;;
unresolved)
	printf 'Unable to satisfy the offline dependency plan.\n' >&2
	exit 100
	;;
*) printf '0 upgraded, 3 newly installed, 0 to remove and 0 not upgraded.\n' ;;
esac
SH
chmod +x "${tmp}/bin/"*

for package in \
	'python3_3.12.3_amd64.deb' \
	'nfs-common_2.6.4_amd64.deb' \
	'cifs-utils_2:7.0_amd64.deb' \
	'rpcbind_1.2.6_amd64.deb'; do
	: >"${tmp}/debs/${package}"
done

PATH="${tmp}/bin:${PATH}"
export PATH
export HFL_NAS_TEST_STATE="${tmp}"

NAS_DEB_FILES=()
select_missing_nas_debs "${tmp}/debs"
[[ "${#NAS_DEB_FILES[@]}" -eq 3 ]]
[[ " ${NAS_DEB_FILES[*]} " != *'python3_3.12.3_amd64.deb'* ]]
[[ " ${NAS_DEB_FILES[*]} " == *'rpcbind_1.2.6_amd64.deb'* ]]
validate_offline_nas_plan "${NAS_DEB_FILES[@]}"
grep -F 'Dir::Etc::sourcelist=/tmp/hfl-nas-plan-' "${tmp}/apt-args" >/dev/null
grep -F 'Dir::Etc::sourceparts=/tmp/hfl-nas-plan-' "${tmp}/apt-args" >/dev/null
grep -F 'cifs-utils_2_7.0_amd64.deb' "${tmp}/apt-args" >/dev/null

for scenario in upgrade downgrade remove; do
	set +e
	(
		exec 3>&1 4>&2
		HFL_TEST_APT_RESULT="${scenario}" validate_offline_nas_plan "${NAS_DEB_FILES[@]}"
	) >"${tmp}/${scenario}.log" 2>&1
	unsafe_status=$?
	set -e
	[[ "${unsafe_status}" -eq 2 ]]
	grep -F 'cannot be installed without changing existing system packages' "${tmp}/${scenario}.log" >/dev/null
done

set +e
(
	exec 3>&1 4>&2
	HFL_TEST_APT_RESULT=unresolved validate_offline_nas_plan "${NAS_DEB_FILES[@]}"
) >"${tmp}/unresolved.log" 2>&1
unresolved_status=$?
set -e
[[ "${unresolved_status}" -eq 2 ]]
grep -F 'cannot be installed safely from the offline package set' "${tmp}/unresolved.log" >/dev/null

: >"${tmp}/debs/broken_1.0_amd64.deb"
set +e
(
	exec 3>&1 4>&2
	NAS_DEB_FILES=()
	select_missing_nas_debs "${tmp}/debs"
) >"${tmp}/broken.log" 2>&1
broken_status=$?
set -e
[[ "${broken_status}" -eq 2 ]]
grep -F 'package broken is not in a healthy state' "${tmp}/broken.log" >/dev/null

printf 'Offline NAS package plan checks passed.\n'
