#!/usr/bin/env bash
# Regression for empty CURL_TLS under `set -u` (CentOS 7 / Bash < 4.4).
#
# Always:
#   1) host smoke that hfl_download works with CURL_TLS=()
#   2) source contract that agent bootstrap uses the Bash < 4.4-safe expansion
# When HFL_TEST_BASH42=1 (Quality CI sets this):
#   3) run the same expansion checks inside Docker bash:4.2
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/docker-images.sh
source "${ROOT}/tools/lib/docker-images.sh"

bootstrap="${ROOT}/deploy/bootstrap/agent-bootstrap-linux.sh"

grep -F '${CURL_TLS[@]+"${CURL_TLS[@]}"}' "${bootstrap}" >/dev/null

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin"

cat >"${tmp}/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
output=""
while (($#)); do
	case "$1" in
	-o)
		output=${2-}
		shift 2
		;;
	*) shift ;;
	esac
done
[[ -n "${output}" ]] || exit 2
printf 'nounset-fixture\n' >"${output}"
SH
chmod +x "${tmp}/bin/curl"

# Host Bash smoke: empty CURL_TLS must still drive hfl_download successfully.
source <(sed -n '/^hfl_now()/,/^hfl_build_enroll_args()/p' "${bootstrap}" | sed '$d')
CURL_TLS=()
PATH="${tmp}/bin:${PATH}"
export PATH
destination="${tmp}/hfl-enroll"
hfl_download "HyperFileLens enrollment helper" https://example.invalid/helper "${destination}" >/dev/null
grep -Fx 'nounset-fixture' "${destination}" >/dev/null

# CentOS 7 class probe. Quality CI sets HFL_TEST_BASH42=1.
if [[ "${HFL_TEST_BASH42:-0}" == "1" ]]; then
	if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
		printf 'ERROR: HFL_TEST_BASH42=1 requires a working docker daemon.\n' >&2
		exit 1
	fi
	# Pull with the shared retry helper so Hub blips do not flake Quality.
	if ! hfl_docker_pull_with_retry bash:4.2 "" 180 3 5; then
		printf 'ERROR: failed to pull bash:4.2: %s\n' "${HFL_DOCKER_LAST_ERROR:-unknown error}" >&2
		exit 1
	fi
	cat >"${tmp}/probe.sh" <<'SH'
set -euo pipefail
CURL_TLS=()
# Old unsafe form must fail on Bash 4.2.
if (set +e; set -u; : "${CURL_TLS[@]}"; status=$?; set +u; exit "${status}") 2>/dev/null; then
	echo "ERROR: expected Bash 4.2 unbound failure for \"\${CURL_TLS[@]}\"" >&2
	exit 1
fi
# Fixed form must succeed for empty and -k.
curl_args=(${CURL_TLS[@]+"${CURL_TLS[@]}"})
[[ "${#curl_args[@]}" -eq 0 ]]
CURL_TLS=(-k)
curl_args=(${CURL_TLS[@]+"${CURL_TLS[@]}"})
[[ "${#curl_args[@]}" -eq 1 && "${curl_args[0]}" == "-k" ]]
# The optional retry flag uses the same safe expansion on Bash 4.2.
retry_connrefused=()
curl_args=(${retry_connrefused[@]+"${retry_connrefused[@]}"})
[[ "${#curl_args[@]}" -eq 0 ]]
retry_connrefused=(--retry-connrefused)
curl_args=(${retry_connrefused[@]+"${retry_connrefused[@]}"})
[[ "${#curl_args[@]}" -eq 1 && "${curl_args[0]}" == "--retry-connrefused" ]]
printf 'ok\n'
SH
	docker run --rm -v "${tmp}/probe.sh:/probe.sh:ro" bash:4.2 \
		bash /probe.sh >/dev/null
	printf 'Bash 4.2 empty CURL_TLS probe passed.\n'
fi

printf 'Bootstrap CURL_TLS nounset checks passed.\n'
