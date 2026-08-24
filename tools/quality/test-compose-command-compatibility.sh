#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin"

cat >"${tmp}/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-} ${2:-} ${3:-}" in
"compose version --short")
	[[ -n "${HFL_FAKE_PLUGIN_VERSION:-}" ]] || exit 1
	printf '%s\n' "${HFL_FAKE_PLUGIN_VERSION}"
	;;
"compose version ")
	[[ -n "${HFL_FAKE_PLUGIN_VERSION:-}" ]] || exit 1
	printf 'Docker Compose version %s\n' "${HFL_FAKE_PLUGIN_VERSION}"
	;;
"info  ") exit 0 ;;
"version --format {{.Server.Version}}") printf '%s\n' "${HFL_FAKE_ENGINE_VERSION:-24.0.0}" ;;
*) exit 1 ;;
esac
SH

cat >"${tmp}/bin/docker-compose" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "version" ]] || exit 1
[[ -n "${HFL_FAKE_STANDALONE_VERSION:-}" ]] || exit 1
if [[ "${2:-}" == "--short" ]]; then
	[[ "${HFL_FAKE_STANDALONE_SHORT_FAIL:-0}" != "1" ]] || exit 1
	printf '%s\n' "${HFL_FAKE_STANDALONE_VERSION}"
else
	printf 'Docker Compose version %s\n' "${HFL_FAKE_STANDALONE_VERSION}"
fi
SH
chmod +x "${tmp}/bin/docker" "${tmp}/bin/docker-compose"

run_release_resolver() (
	export PATH="${tmp}/bin:${PATH}"
	export HFL_FAKE_PLUGIN_VERSION="${1:-}" HFL_FAKE_STANDALONE_VERSION="${2:-}"
	# shellcheck source=../../deploy/installer/compose-runtime.sh
	source "${ROOT}/deploy/installer/compose-runtime.sh"
	hfl_compose_resolve 2.20.0
	[[ "${HFL_COMPOSE[*]}" == "$3" ]]
	[[ "${HFL_COMPOSE_VERSION}" == "$4" ]]
)

run_release_resolver v2.24.1 V5.0.1 'docker compose' 2.24.1
run_release_resolver '' V5.0.1 docker-compose 5.0.1
run_release_resolver 2.19.9 5.0.1 docker-compose 5.0.1
HFL_FAKE_STANDALONE_SHORT_FAIL=1 run_release_resolver '' v5.0.1 docker-compose 5.0.1

if run_release_resolver 2.19.9 1.29.2 docker-compose 1.29.2; then
	printf 'Unsupported Compose versions were accepted\n' >&2
	exit 1
fi

(
	export PATH="${tmp}/bin:${PATH}"
	export HFL_FAKE_PLUGIN_VERSION='' HFL_FAKE_STANDALONE_VERSION=5.0.1
	# shellcheck source=../../deploy/bootstrap/gateway-install-docker-ubuntu-amd64.sh
	source "${ROOT}/deploy/bootstrap/gateway-install-docker-ubuntu-amd64.sh"
	docker_runtime_ready 24.0.0
	[[ "${COMPOSE[*]}" == docker-compose ]]
)

(
	export PATH="${tmp}/bin:${PATH}"
	export HFL_FAKE_PLUGIN_VERSION='' HFL_FAKE_STANDALONE_VERSION=5.0.1
	# shellcheck source=../../deploy/bootstrap/gateway-lifecycle.sh
	source "${ROOT}/deploy/bootstrap/gateway-lifecycle.sh"
	resolve_compose
	[[ "${COMPOSE[*]}" == docker-compose ]]
)

sidecar="${ROOT}/deploy/bootstrap/gateway-install-lensnode-sidecar.sh"
grep -F 'resolve_compose' "${sidecar}" >/dev/null
grep -F '"${COMPOSE[@]}" -p "${COMPOSE_PROJECT}"' "${sidecar}" >/dev/null

printf 'Compose command compatibility checks passed.\n'
