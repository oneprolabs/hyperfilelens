#!/usr/bin/env bash
# Run a pinned Playwright package against the local development stack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../lib/env-file.sh
source "${ROOT}/tools/lib/env-file.sh"
# shellcheck source=../lib/docker-images.sh
source "${ROOT}/tools/lib/docker-images.sh"

hfl_env_select_repo_file "${ROOT}"
read_default() {
	local key=$1 fallback=$2 value
	if [[ -n "${!key:-}" ]]; then
		printf '%s' "${!key}"
		return 0
	fi
	value="$(hfl_env_read "${key}")"
	printf '%s' "${value:-${fallback}}"
}

read_file_default() {
	local env_file=$1 key=$2 fallback=$3 previous="${HFL_ENV_FILE}" value
	HFL_ENV_FILE="${env_file}"
	value="$(hfl_env_read "${key}")"
	HFL_ENV_FILE="${previous}"
	printf '%s' "${value:-${fallback}}"
}

resolve_smoke_edition() {
	local edition="${HFL_RELEASE_EDITION:-}" api_container runtime_env="" extensions="" line
	if [[ -z "${edition}" ]]; then
		api_container="$(
			docker compose \
				--project-directory "${ROOT}" \
				--env-file "${ROOT}/.env" \
				-f "${ROOT}/docker-compose.yml" \
				ps -q api 2>/dev/null
		)"
		[[ -n "${api_container}" && "${api_container}" != *$'\n'* ]] || {
			printf 'ERROR: cannot identify one running API container; set HFL_RELEASE_EDITION explicitly\n' >&2
			return 2
		}
		runtime_env="$(
			docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
				"${api_container}"
		)" || {
			printf 'ERROR: cannot inspect the running API container\n' >&2
			return 1
		}
		while IFS= read -r line; do
			case "${line}" in
			HFL_EXTENSIONS=*) extensions="${line#HFL_EXTENSIONS=}" ;;
			esac
		done <<<"${runtime_env}"
		edition=community
		[[ -z "${extensions}" ]] || edition=enterprise
	fi
	case "${edition}" in
	community | enterprise) printf '%s' "${edition}" ;;
	*)
		printf 'ERROR: unsupported HFL_RELEASE_EDITION=%s\n' "${edition}" >&2
		return 2
		;;
	esac
}

version="$(read_default DEV_SMOKE_PLAYWRIGHT_VERSION 1.55.0)"
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
	|| { printf 'ERROR: invalid DEV_SMOKE_PLAYWRIGHT_VERSION=%s\n' "${version}" >&2; exit 2; }
image="mcr.microsoft.com/playwright:v${version}-noble"
offline="$(read_default DEV_OFFLINE 0)"
timeout_seconds="$(read_default DEV_SMOKE_PULL_TIMEOUT_SECONDS 900)"
retries="$(read_default DOCKER_PULL_RETRIES 2)"
smoke_edition="$(resolve_smoke_edition)"

if ! hfl_docker_ensure_image "${image}" "" 0 "${offline}" "linux/amd64" \
	"${timeout_seconds}" "${retries}"; then
	printf 'ERROR: unable to prepare Playwright image: %s\n' "${HFL_DOCKER_LAST_ERROR}" >&2
	exit 1
fi

cache_dir="${ROOT}/build/cache/playwright-node-${version}"
mkdir -p "${cache_dir}"
source_lens_env="${SMOKE_SOURCELENS_ENV_FILE:-${ROOT}/data/sourcelens/config/.env}"
source_lens_user="${SOURCELENS_USER:-}"
if [[ -z "${source_lens_user}" ]]; then
	source_lens_user="$(read_file_default "${source_lens_env}" DJANGO_SUPERUSER_EMAIL '')"
fi
if [[ -z "${source_lens_user}" ]]; then
	source_lens_user="$(read_file_default "${source_lens_env}" DJANGO_SUPERUSER_USERNAME admin)"
fi
smoke_host="${SMOKE_HOST:-host.docker.internal}"
tenant_port="$(read_default HFL_TENANT_PORT 11443)"
login_port="$(read_default HFL_LOGIN_PORT "${tenant_port}")"

docker run --rm \
	--add-host host.docker.internal:host-gateway \
	-v "${ROOT}:/workspace:ro" \
	-v "${cache_dir}:/smoke" \
	-e PLAYWRIGHT_VERSION="${version}" \
	-e DEV_OFFLINE="${offline}" \
	-e SMOKE_HOST="${smoke_host}" \
	-e HFL_TENANT_PORT="${tenant_port}" \
	-e HFL_ADMIN_PORT="$(read_default HFL_ADMIN_PORT 11444)" \
	-e HFL_LOGIN_PORT="${login_port}" \
	-e SOURCELENS_CONSOLE_PORT="$(read_default SOURCELENS_CONSOLE_PORT 11445)" \
	-e SEED_ADMIN_EMAIL="$(read_default SEED_ADMIN_EMAIL admin@hyperfilelens.com)" \
	-e SEED_ADMIN_PASSWORD="$(read_default SEED_ADMIN_PASSWORD 'Admin@123')" \
	-e SOURCELENS_USER="${source_lens_user}" \
	-e SOURCELENS_PASSWORD="$(read_file_default "${source_lens_env}" DJANGO_SUPERUSER_PASSWORD adminpassword)" \
	-e SMOKE_REQUIRE_HMR="${SMOKE_REQUIRE_HMR:-1}" \
	-e SMOKE_SKIP_SOURCELENS="${SMOKE_SKIP_SOURCELENS:-0}" \
	-e HFL_RELEASE_EDITION="${smoke_edition}" \
	"${image}" bash -euc '
		cd /smoke
		if [[ ! -f package.json ]]; then
			printf "%s\n" "{\"private\":true}" > package.json
		fi
		if [[ ! -f node_modules/playwright/package.json ]] \
			|| [[ "$(node -p "require(\"./node_modules/playwright/package.json\").version")" != "${PLAYWRIGHT_VERSION}" ]]; then
			if [[ "${DEV_OFFLINE}" == "1" ]]; then
				printf "%s\n" "ERROR: Playwright package cache is missing in offline mode" >&2
				exit 1
			fi
			npm install --no-audit --no-fund --no-package-lock --no-save "playwright@${PLAYWRIGHT_VERSION}"
		fi
		node /workspace/tools/dev/browser-smoke.mjs
	'
