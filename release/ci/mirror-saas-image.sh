#!/usr/bin/env bash
# Copy an immutable image manifest to the secondary registry and verify its digest.
set -euo pipefail

[[ $# -eq 3 ]] || {
	printf 'Usage: %s SOURCE_REF DIGEST DESTINATION_REF\n' "$0" >&2
	exit 2
}

source_ref=$1
digest=$2
destination_ref=$3
[[ "${source_ref}" == */*:* && "${destination_ref}" == */*:* ]]
[[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]

source_repository=${source_ref%:*}
docker buildx imagetools create \
	--prefer-index=false \
	--tag "${destination_ref}" \
	"${source_repository}@${digest}"

manifest_json="$(docker buildx imagetools inspect \
	"${destination_ref}" --format '{{json .Manifest}}')"
destination_digest="$(jq -r '.digest // empty' <<<"${manifest_json}")"
[[ "${destination_digest}" == "${digest}" ]] || {
	printf 'ERROR: mirrored digest mismatch: source=%s destination=%s\n' \
		"${digest}" "${destination_digest:-missing}" >&2
	exit 1
}
