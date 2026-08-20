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
attempts=5
base_delay=${HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS:-5}
jitter_max=${HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS:-3}
[[ "${base_delay}" =~ ^[0-9]+$ && "${jitter_max}" =~ ^[0-9]+$ ]] || {
	printf 'ERROR: registry mirror retry timing must use non-negative integers\n' >&2
	exit 2
}

copy_log="$(mktemp)"
inspect_log="$(mktemp)"
inspect_output="$(mktemp)"
trap 'rm -f "${copy_log}" "${inspect_log}" "${inspect_output}"' EXIT

mirror_error_is_retryable() {
	grep -Eiq \
		'too many requests|(status|response|http)[^[:cntrl:]]*(408|429|500|502|503|504)|request timeout|timeout|deadline exceeded|connection (reset|refused)|unexpected eof|tls handshake timeout|temporary failure|i/o timeout|network is unreachable|no such host' \
		"$@"
}

wait_before_retry() {
	local failed_attempt=$1
	local operation=$2
	local delay jitter wait_seconds

	delay=$((base_delay * (1 << (failed_attempt - 1))))
	((delay <= 40)) || delay=40
	jitter=0
	((jitter_max == 0)) || jitter=$((RANDOM % (jitter_max + 1)))
	wait_seconds=$((delay + jitter))
	printf 'WARN: temporary registry %s failure; retrying in %ss\n' \
		"${operation}" "${wait_seconds}" >&2
	sleep "${wait_seconds}"
}

copy_status=1
for ((attempt = 1; attempt <= attempts; attempt++)); do
	: >"${copy_log}"
	printf '[....] Mirroring image (attempt %s/%s): %s -> %s\n' \
		"${attempt}" "${attempts}" "${source_repository}@${digest}" "${destination_ref}"
	if docker buildx imagetools create \
		--prefer-index=false \
		--tag "${destination_ref}" \
		"${source_repository}@${digest}" 2>&1 | tee "${copy_log}"; then
		pipeline_status=("${PIPESTATUS[@]}")
	else
		pipeline_status=("${PIPESTATUS[@]}")
	fi
	copy_status=${pipeline_status[0]:-1}
	tee_status=${pipeline_status[1]:-1}
	if ((tee_status != 0)); then
		printf 'ERROR: failed to record registry mirror output\n' >&2
		exit "${tee_status}"
	fi
	if ((copy_status == 0)); then
		break
	fi
	if ! mirror_error_is_retryable "${copy_log}"; then
		printf 'ERROR: registry mirror failed with a non-retryable error\n' >&2
		exit "${copy_status}"
	fi
	if ((attempt == attempts)); then
		break
	fi
	wait_before_retry "${attempt}" mirror
done
((copy_status == 0)) || {
	printf 'ERROR: registry mirror failed after %s attempts\n' "${attempts}" >&2
	exit "${copy_status}"
}

manifest_json=
inspect_status=1
for ((attempt = 1; attempt <= attempts; attempt++)); do
	: >"${inspect_log}"
	: >"${inspect_output}"
	printf '[....] Verifying mirrored image (attempt %s/%s): %s\n' \
		"${attempt}" "${attempts}" "${destination_ref}"
	if docker buildx imagetools inspect \
		"${destination_ref}" --format '{{json .Manifest}}' \
		>"${inspect_output}" 2>"${inspect_log}"; then
		inspect_status=0
		manifest_json="$(<"${inspect_output}")"
		break
	else
		inspect_status=$?
	fi
	[[ ! -s "${inspect_output}" ]] || cat "${inspect_output}" >&2
	[[ ! -s "${inspect_log}" ]] || cat "${inspect_log}" >&2
	if ! mirror_error_is_retryable "${inspect_output}" "${inspect_log}"; then
		printf 'ERROR: registry mirror verification failed with a non-retryable error\n' >&2
		exit "${inspect_status}"
	fi
	if ((attempt == attempts)); then
		break
	fi
	wait_before_retry "${attempt}" verification
done
((inspect_status == 0)) || {
	printf 'ERROR: registry mirror verification failed after %s attempts\n' \
		"${attempts}" >&2
	exit "${inspect_status}"
}
destination_digest="$(jq -r '.digest // empty' <<<"${manifest_json}")"
[[ "${destination_digest}" == "${digest}" ]] || {
	printf 'ERROR: mirrored digest mismatch: source=%s destination=%s\n' \
		"${digest}" "${destination_digest:-missing}" >&2
	exit 1
}
