#!/usr/bin/env bash
# Build one HFL-bundled SourceLens component and push it to the HFL registry namespace.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../../tools/sourcelens/common.sh
source "${ROOT}/tools/sourcelens/common.sh"
# shellcheck source=../../tools/lib/version.sh
source "${ROOT}/tools/lib/version.sh"

usage() {
	printf 'Usage: %s COMPONENT REGISTRY_HOST/NAMESPACE HFL_VERSION OUTPUT_JSON\n' "$0" >&2
}

[[ $# -eq 4 ]] || { usage; exit 2; }
component=$1
registry_prefix=${2%/}
hfl_version=${3#v}
output=$4

case "${component}" in
backend) service=backend-api ;;
frontend) service=frontend ;;
lensnode) service=lensnode ;;
*) printf 'ERROR: unsupported SourceLens component: %s\n' "${component}" >&2; exit 2 ;;
esac
[[ "${registry_prefix}" == */* ]] || {
	printf 'ERROR: registry prefix must include host and namespace: %s\n' "${registry_prefix}" >&2
	exit 2
}
registry_host=${registry_prefix%%/*}
registry_namespace=${registry_prefix#*/}
[[ "${registry_host}" =~ ^[a-z0-9][a-z0-9.-]*(:[0-9]+)?$ ]] || {
	printf 'ERROR: registry host is invalid: %s\n' "${registry_host}" >&2
	exit 2
}
[[ "${registry_namespace}" =~ ^[a-z0-9][a-z0-9._/-]*$ \
	&& "${registry_namespace}" != */ && "${registry_namespace}" != *//* ]] || {
	printf 'ERROR: registry namespace is invalid: %s\n' "${registry_namespace}" >&2
	exit 2
}
hfl_version="$(normalize_artifact_id "${hfl_version}")"

sourcelens_load_config
SOURCELENS_HFL_VERSION="${hfl_version}"
sourcelens_resolve_version
hfl_logging_configure "ci-sourcelens-${component}"
hfl_logging_start
trap 'hfl_logging_finish "$?"' EXIT

sourcelens_sync_source
target_ref="${registry_prefix}/hyperfilelens-sourcelens-${component}:${SOURCELENS_DISTRIBUTION_TAG}"
source_commit="$(git -C "${SOURCELENS_SOURCE_CACHE}" rev-parse HEAD)"
fingerprint="$({
	sourcelens_component_build_identity "${component}" "${source_commit}"
	find \
		"${ROOT}/tools/sourcelens" \
		"${ROOT}/deploy/installer/sourcelens" \
		"${ROOT}/release/build-sourcelens.sh" \
		"${ROOT}/release/ci/build-sourcelens-image.sh" \
		-path "${ROOT}/tools/sourcelens/patches/retired" -prune -o \
		-type f -print0 \
		| sort -z \
		| xargs -0 sha256sum
} | sha256sum | cut -c1-24)"
cache_ref="${registry_prefix}/hyperfilelens-sourcelens-${component}:slcache-${fingerprint}"
cache_hit=false
cache_manifest_json="$(docker buildx imagetools inspect "${cache_ref}" \
	--format '{{json .Manifest}}' 2>/dev/null || true)"
cache_digest="$(jq -r '.digest // empty' <<<"${cache_manifest_json:-{}}" 2>/dev/null || true)"

if [[ "${cache_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
	cache_hit=true
	printf 'Reusing immutable SourceLens component %s from %s\n' "${component}" "${cache_ref}"
	docker buildx imagetools create --tag "${target_ref}" "${cache_ref}"
else
	SOURCELENS_BUILD_SERVICES="${service}" sourcelens_build_app_images 1 0
	local_ref="$(sourcelens_upstream_image_ref "${component}")"
	docker image inspect "${local_ref}" >/dev/null
	docker tag "${local_ref}" "${target_ref}"
	docker tag "${local_ref}" "${cache_ref}"
	docker push "${target_ref}"
	docker push "${cache_ref}"
fi

if [[ "${component}" == "frontend" ]]; then
	if ! SOURCELENS_BUILD_SOURCE_MAPS=1 \
		"${ROOT}/release/ci/upload-sourcelens-sourcemaps.sh" \
		"hyperfilelens-sourcelens-frontend@${SOURCELENS_DISTRIBUTION_TAG}"; then
		printf '::warning title=Sentry Source Maps::Bundled SourceLens symbol processing failed; the image build will continue.\n'
	fi
fi

manifest_json="$(docker buildx imagetools inspect "${target_ref}" --format '{{json .Manifest}}')"
digest="$(jq -r '.digest // empty' <<<"${manifest_json}")"
[[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]] || {
	printf 'ERROR: unable to resolve pushed digest for %s\n' "${target_ref}" >&2
	exit 1
}

mkdir -p "$(dirname "${output}")"
jq -n \
	--arg component "sourcelens-${component}" \
	--arg ref "${target_ref}" \
	--arg digest "${digest}" \
	--arg platform linux/amd64 \
	--arg sourcelens_version "${SOURCELENS_VERSION}" \
	--arg sourcelens_git_ref "${SOURCELENS_GIT_REF}" \
	--arg sourcelens_git_commit "${source_commit}" \
	--arg build_fingerprint "${fingerprint}" \
	--arg cache_ref "${cache_ref}" \
	--argjson cache_hit "${cache_hit}" \
	'{
	  component: $component,
	  ref: $ref,
	  digest: $digest,
	  platform: $platform,
	  sourcelens_version: $sourcelens_version,
	  sourcelens_git_ref: $sourcelens_git_ref,
	  sourcelens_git_commit: $sourcelens_git_commit,
	  build_fingerprint: $build_fingerprint,
	  cache_ref: $cache_ref,
	  cache_hit: $cache_hit
	}' >"${output}"
