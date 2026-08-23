#!/usr/bin/env bash
# Validate interactive bootstrap download progress, retries, and partial-file cleanup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bootstrap="${ROOT}/deploy/bootstrap/agent-bootstrap-linux.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin"

cat >"${tmp}/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--retry-connrefused" && "${2:-}" == "--version" ]]; then
	if [[ "${HFL_TEST_CURL_SUPPORT_RETRY_CONNREFUSED:-0}" == "1" ]]; then
		exit 0
	fi
	printf 'curl: option --retry-connrefused: is unknown\n' >&2
	exit 2
fi
output=""
headers=""
while (($#)); do
	printf '%s\n' "$1" >>"${HFL_TEST_CURL_LOG}"
	case "$1" in
	-o)
		output=${2-}
		printf '%s\n' "${2-}" >>"${HFL_TEST_CURL_LOG}"
		shift 2
		;;
	--dump-header)
		headers=${2-}
		printf '%s\n' "${2-}" >>"${HFL_TEST_CURL_LOG}"
		shift 2
		;;
	*) shift ;;
	esac
done
[[ -n "${output}" ]] || exit 2
if [[ -n "${headers}" ]]; then
	printf 'HTTP/1.1 200 OK\r\nContent-Length: 27\r\n\r\n' >"${headers}"
fi
printf 'bootstrap-download-fixture\n' >"${output}"
if [[ "${HFL_TEST_CURL_FAIL:-0}" == "1" ]]; then
	exit 7
fi
SH
chmod +x "${tmp}/bin/curl"

# Load only the standalone bootstrap logging and download helpers.
source <(sed -n '/^hfl_fail()/,/^hfl_build_enroll_args()/p' "${bootstrap}" | sed '$d')
CURL_TLS=()
PATH="${tmp}/bin:${PATH}"
export PATH HFL_TEST_CURL_LOG="${tmp}/curl.log"
export HFL_TEST_CURL_SUPPORT_RETRY_CONNREFUSED=1

destination="${tmp}/hfl-enroll"
output="$(hfl_download "HyperFileLens enrollment helper" https://example.invalid/helper "${destination}" 2>&1)"
grep -F '[....] HyperFileLens enrollment helper' <<<"${output}" >/dev/null
grep -F '[####################] | 100% | 27 B / 27 B' <<<"${output}" >/dev/null
grep -F '[ OK ] HyperFileLens enrollment helper downloaded (' <<<"${output}" >/dev/null
grep -Fx -- '--silent' "${HFL_TEST_CURL_LOG}" >/dev/null
if grep -Fx -- '--progress-bar' "${HFL_TEST_CURL_LOG}" >/dev/null; then
	printf 'ERROR: bootstrap must not expose curl native progress output\n' >&2
	exit 1
fi
grep -Fx -- '--retry' "${HFL_TEST_CURL_LOG}" >/dev/null
grep -Fx -- '--retry-connrefused' "${HFL_TEST_CURL_LOG}" >/dev/null
grep -Fx 'bootstrap-download-fixture' "${destination}" >/dev/null
[[ ! -e "${destination}.part" ]]

# curl 7.29 (CentOS 7) must download without the unsupported optional flag.
rm -f "${destination}"
: >"${HFL_TEST_CURL_LOG}"
export HFL_TEST_CURL_SUPPORT_RETRY_CONNREFUSED=0
output="$(hfl_download "HyperFileLens enrollment helper" https://example.invalid/helper "${destination}" 2>&1)"
grep -F '[ OK ] HyperFileLens enrollment helper downloaded (' <<<"${output}" >/dev/null
grep -Fx -- '--retry' "${HFL_TEST_CURL_LOG}" >/dev/null
grep -Fx -- '--retry-delay' "${HFL_TEST_CURL_LOG}" >/dev/null
if grep -Fx -- '--retry-connrefused' "${HFL_TEST_CURL_LOG}" >/dev/null; then
	printf 'ERROR: unsupported --retry-connrefused was passed to a legacy curl\n' >&2
	exit 1
fi
grep -Fx 'bootstrap-download-fixture' "${destination}" >/dev/null
[[ ! -e "${destination}.part" ]]

rm -f "${destination}"
: >"${HFL_TEST_CURL_LOG}"
export HFL_TEST_CURL_FAIL=1
set +e
(hfl_download "HyperFileLens enrollment helper" https://example.invalid/helper "${destination}") \
	>"${tmp}/failed.log" 2>&1
status=$?
set -e
[[ "${status}" -eq 3 ]]
grep -F '[FAIL] Failed to download HyperFileLens enrollment helper.' "${tmp}/failed.log" >/dev/null
[[ ! -e "${destination}" ]]
[[ ! -e "${destination}.part" ]]

printf 'Bootstrap download progress checks passed.\n'
