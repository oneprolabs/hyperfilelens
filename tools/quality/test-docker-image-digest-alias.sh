#!/usr/bin/env bash
# Verify digest-pinned official pulls also create the mutable local alias expected by consumers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/docker-images.sh
source "${ROOT}/tools/lib/docker-images.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin" "${tmp}/state"

cat >"${tmp}/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
image)
	[[ "${2:-}" == "inspect" ]]
	if [[ -f "${HFL_DOCKER_TEST_STATE}/legacy-local" ]]; then
		[[ " $* " != *" --platform "* ]] || exit 1
		printf 'linux/amd64\n'
		exit 0
	fi
	if [[ -f "${HFL_DOCKER_TEST_STATE}/local-index" ]]; then
		[[ " $* " == *" --platform linux/amd64 "* ]] || exit 1
		printf 'linux/amd64\n'
		exit 0
	fi
	[[ -f "${HFL_DOCKER_TEST_STATE}/pulled" ]] || exit 1
	printf 'linux/amd64\n'
	;;
pull)
	touch "${HFL_DOCKER_TEST_STATE}/pulled"
	;;
tag)
	printf '%s\t%s\n' "$2" "$3" >>"${HFL_DOCKER_TEST_STATE}/tags"
	;;
*)
	printf 'unexpected fake docker command: %s\n' "$*" >&2
	exit 2
	;;
esac
SH
chmod +x "${tmp}/bin/docker"

export HFL_DOCKER_TEST_STATE="${tmp}/state"
PATH="${tmp}/bin:${PATH}"
export PATH

touch "${HFL_DOCKER_TEST_STATE}/local-index"
hfl_docker_image_matches_platform redis:alpine linux/amd64
rm -f "${HFL_DOCKER_TEST_STATE}/local-index"

touch "${HFL_DOCKER_TEST_STATE}/legacy-local"
hfl_docker_image_matches_platform redis:alpine linux/amd64
rm -f "${HFL_DOCKER_TEST_STATE}/legacy-local"

digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
source_ref="nginx:stable-alpine@${digest}"
hfl_docker_ensure_image "${source_ref}" "" 0 0 linux/amd64 5 1

[[ "${HFL_DOCKER_IMAGE_SOURCE}" == "official" ]]
grep -Fx "${source_ref}"$'\t'"nginx:stable-alpine" "${tmp}/state/tags" >/dev/null

printf 'Digest-pinned Docker image alias checks passed.\n'
