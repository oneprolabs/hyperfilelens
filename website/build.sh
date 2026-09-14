#!/usr/bin/env bash
# Build the standalone Website into a version-neutral static artifact directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT=""
IMAGE_TAG="${HFL_WEBSITE_BUILDER_IMAGE:-hyperfilelens-website-builder:dev}"
PLATFORM="${HFL_WEBSITE_BUILD_PLATFORM:-linux/amd64}"
NPM_REGISTRY="${NPM_REGISTRY:-}"
BASE_IMAGE="${HFL_WEBSITE_BASE_IMAGE:-node:22-alpine}"
NO_CACHE=0
PULL=0

usage() {
	cat <<'USAGE'
Usage: ./website/build.sh --output DIRECTORY [options]

Options:
  --output DIRECTORY   Destination for the static Website artifact
  --image-tag IMAGE    Local builder image tag
  --platform PLATFORM  Docker build platform (default: linux/amd64)
  --npm-registry URL   Optional npm registry used only while building
  --base-image IMAGE   Optional Dockerfile base image (default: node:22-alpine)
  --no-cache           Rebuild the Website builder without Docker cache
  --pull               Refresh the Website builder base image
USAGE
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--output) OUTPUT=${2:-}; shift 2 ;;
	--image-tag) IMAGE_TAG=${2:-}; shift 2 ;;
	--platform) PLATFORM=${2:-}; shift 2 ;;
	--npm-registry) NPM_REGISTRY=${2:-}; shift 2 ;;
	--base-image) BASE_IMAGE=${2:-}; shift 2 ;;
	--no-cache) NO_CACHE=1; shift ;;
	--pull) PULL=1; shift ;;
	-h | --help) usage; exit 0 ;;
	*) printf 'ERROR: unknown argument: %s\n' "$1" >&2; exit 2 ;;
	esac
done

[[ -n "${OUTPUT}" ]] || { printf 'ERROR: --output is required\n' >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { printf 'ERROR: docker is required\n' >&2; exit 2; }
docker info >/dev/null 2>&1 || { printf 'ERROR: Docker daemon is not reachable\n' >&2; exit 1; }

mkdir -p "$(dirname "${OUTPUT}")"
output_parent="$(cd "$(dirname "${OUTPUT}")" && pwd)"
output_name="$(basename "${OUTPUT}")"
[[ "${output_name}" != "." && "${output_name}" != ".." && "${output_name}" != "/" ]] \
	|| { printf 'ERROR: unsafe output directory: %s\n' "${OUTPUT}" >&2; exit 2; }
OUTPUT="${output_parent}/${output_name}"
if [[ -e "${OUTPUT}" && ! -f "${OUTPUT}/.hfl-website-artifact" ]]; then
	printf 'ERROR: refusing to replace unrecognized output directory: %s\n' "${OUTPUT}" >&2
	exit 1
fi
temporary="$(mktemp -d "${output_parent}/.${output_name}.tmp.XXXXXX")"
container_id=""

cleanup() {
	[[ -z "${container_id}" ]] || docker rm -f "${container_id}" >/dev/null 2>&1 || true
	[[ -z "${temporary}" ]] || rm -rf -- "${temporary}"
}
trap cleanup EXIT

build_args=(
	build
	--platform "${PLATFORM}"
	--file "${SCRIPT_DIR}/Dockerfile"
	--tag "${IMAGE_TAG}"
	--build-arg "NPM_REGISTRY=${NPM_REGISTRY}"
	--build-arg "WEBSITE_BASE_IMAGE=${BASE_IMAGE}"
)
[[ "${NO_CACHE}" -eq 0 ]] || build_args+=(--no-cache)
[[ "${PULL}" -eq 0 ]] || build_args+=(--pull)
build_args+=("${SCRIPT_DIR}")

docker "${build_args[@]}"
container_id="$(docker create --platform "${PLATFORM}" "${IMAGE_TAG}")"
mkdir -p "${temporary}/public"
docker cp "${container_id}:/website/.vitepress/dist/." "${temporary}/public/"
docker rm -f "${container_id}" >/dev/null
container_id=""
install -m 0755 "${SCRIPT_DIR}/runtime-config.sh" "${temporary}/runtime-config.sh"

[[ -f "${temporary}/public/index.html" ]] \
	|| { printf 'ERROR: Website artifact is missing index.html\n' >&2; exit 1; }
[[ -f "${temporary}/public/website-runtime-config.js" ]] \
	|| { printf 'ERROR: Website artifact is missing website-runtime-config.js\n' >&2; exit 1; }
printf 'schema=1\n' >"${temporary}/.hfl-website-artifact"
find "${temporary}" -type d -exec chmod 0755 {} +
find "${temporary}" -type f -exec chmod 0644 {} +
chmod 0755 "${temporary}/runtime-config.sh"

if [[ -e "${OUTPUT}" && ! -f "${OUTPUT}/.hfl-website-artifact" ]]; then
	printf 'ERROR: refusing to replace unrecognized output directory: %s\n' "${OUTPUT}" >&2
	exit 1
fi
[[ ! -e "${OUTPUT}" ]] || rm -rf -- "${OUTPUT}"
mv "${temporary}" "${OUTPUT}"
temporary=""
printf 'Website static artifact written to %s\n' "${OUTPUT}"
