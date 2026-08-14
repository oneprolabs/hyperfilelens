#!/usr/bin/env bash
# Shared Docker image resolution with local reuse, mirror fallback, retries, and timeouts.

if [[ "${_hfl_docker_images_loaded:-0}" == "1" ]]; then
	return 0 2>/dev/null || exit 0
fi
_hfl_docker_images_loaded=1

HFL_DOCKER_IMAGE_SOURCE=""
HFL_DOCKER_LAST_ERROR=""

hfl_docker_normalize_mirror_host() {
	local mirror="${1:-}"
	mirror="${mirror#https://}"
	mirror="${mirror#http://}"
	mirror="${mirror%/}"
	printf '%s' "${mirror}"
}

hfl_docker_mirror_image_ref() {
	local image=$1 mirror_host=$2
	if [[ -z "${mirror_host}" ]]; then
		printf '%s' "${image}"
	elif [[ "${image}" == */* ]]; then
		printf '%s/%s' "${mirror_host}" "${image}"
	else
		printf '%s/library/%s' "${mirror_host}" "${image}"
	fi
}

hfl_docker_export_build_base_images() {
	local mirror_host
	mirror_host="$(hfl_docker_normalize_mirror_host "${1:-}")"
	export HFL_BACKEND_BASE_IMAGE
	export HFL_FRONTEND_NODE_BASE_IMAGE
	export HFL_FRONTEND_NGINX_BASE_IMAGE
	export HFL_WEBSITE_BASE_IMAGE
	HFL_BACKEND_BASE_IMAGE="$(hfl_docker_mirror_image_ref 'ubuntu:24.04' "${mirror_host}")"
	HFL_FRONTEND_NODE_BASE_IMAGE="$(hfl_docker_mirror_image_ref 'node:22-alpine' "${mirror_host}")"
	HFL_FRONTEND_NGINX_BASE_IMAGE="$(hfl_docker_mirror_image_ref 'nginx:stable-alpine' "${mirror_host}")"
	HFL_WEBSITE_BASE_IMAGE="${HFL_FRONTEND_NODE_BASE_IMAGE}"
}

hfl_docker_validate_pull_settings() {
	local timeout_seconds=${1:-180} retries=${2:-2} retry_delay=${3:-10}
	[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || return 1
	[[ "${retries}" =~ ^[1-9][0-9]*$ ]] || return 1
	[[ "${retry_delay}" =~ ^[0-9]+$ ]] || return 1
	command -v timeout >/dev/null 2>&1 || return 1
}

hfl_docker_start_pull_budget() {
	local budget_seconds=${1:-480}
	[[ "${budget_seconds}" =~ ^[1-9][0-9]*$ ]] || {
		HFL_DOCKER_LAST_ERROR="invalid Docker pull budget: ${budget_seconds}"
		return 2
	}
	HFL_DOCKER_PULL_DEADLINE_SECONDS=$((SECONDS + budget_seconds))
}

hfl_docker_pull_error_is_terminal() {
	local log_file=$1
	grep -Eiq \
		'unauthorized|authentication required|pull access denied|requested access .* denied|denied:|manifest unknown|no matching manifest|does not match the specified platform|unsupported platform|no space left on device|invalid reference format|failed to verify checksum|digest invalid|unexpected commit digest' \
		"${log_file}"
}

hfl_docker_image_matches_platform() {
	local image=$1 platform="${2:-}" actual expected
	if [[ -n "${platform}" ]]; then
		# Docker 29 may keep a locally pulled multi-platform tag as an OCI image
		# index. A generic inspect then exposes empty Os/Architecture fields even
		# though the requested platform is present in the local image store.
		actual="$(docker image inspect --platform "${platform}" "${image}" \
			--format '{{.Os}}/{{.Architecture}}' 2>/dev/null || true)"
		# Docker clients predating platform-selective inspect still expose
		# platform fields for their single-platform local image representation.
		[[ -n "${actual}" ]] || actual="$(docker image inspect "${image}" \
			--format '{{.Os}}/{{.Architecture}}' 2>/dev/null || true)"
	else
		actual="$(docker image inspect "${image}" \
			--format '{{.Os}}/{{.Architecture}}' 2>/dev/null || true)"
	fi
	[[ -n "${actual}" ]] || return 1
	[[ -z "${platform}" ]] && return 0
	case "${platform}" in
	*/*/*) expected="${platform%/*}" ;;
	*) expected="${platform}" ;;
	esac
	[[ "${actual}" == "${expected}" ]]
}

hfl_docker_pull_with_retry() {
	local image=$1 platform="${2:-}" timeout_seconds=${3:-180} retries=${4:-2}
	local retry_delay=${5:-10} kill_after_seconds=${HFL_DOCKER_PULL_KILL_AFTER_SECONDS:-10}
	local attempt attempt_timeout remaining pull_status log_file sleep_seconds
	hfl_docker_validate_pull_settings "${timeout_seconds}" "${retries}" "${retry_delay}" || {
		HFL_DOCKER_LAST_ERROR="invalid pull timeout/retry settings or timeout command unavailable"
		return 2
	}
	[[ "${kill_after_seconds}" =~ ^[1-9][0-9]*$ ]] || {
		HFL_DOCKER_LAST_ERROR="invalid Docker pull forced-termination delay: ${kill_after_seconds}"
		return 2
	}
	log_file="$(mktemp)"
	for attempt in $(seq 1 "${retries}"); do
		attempt_timeout=${timeout_seconds}
		if [[ "${HFL_DOCKER_PULL_DEADLINE_SECONDS:-0}" -gt 0 ]]; then
			remaining=$((HFL_DOCKER_PULL_DEADLINE_SECONDS - SECONDS))
			if [[ "${remaining}" -le 0 ]]; then
				HFL_DOCKER_LAST_ERROR="Docker pull budget exhausted before ${image} completed"
				rm -f "${log_file}"
				return 1
			fi
			[[ "${remaining}" -ge "${attempt_timeout}" ]] || attempt_timeout=${remaining}
		fi
		local -a args=(docker pull)
		[[ -n "${platform}" ]] && args+=(--platform "${platform}")
		args+=("${image}")
		: >"${log_file}"
		if timeout --foreground --kill-after="${kill_after_seconds}s" \
			"${attempt_timeout}s" "${args[@]}" 2>&1 | tee "${log_file}"; then
			rm -f "${log_file}"
			return 0
		fi
		pull_status=${PIPESTATUS[0]}
		HFL_DOCKER_LAST_ERROR="pull ${image} failed (attempt ${attempt}/${retries})"
		if hfl_docker_pull_error_is_terminal "${log_file}"; then
			HFL_DOCKER_LAST_ERROR="pull ${image} failed with a non-retryable registry error"
			rm -f "${log_file}"
			return "${pull_status}"
		fi
		if [[ "${attempt}" -lt "${retries}" ]]; then
			sleep_seconds=${retry_delay}
			if [[ "${HFL_DOCKER_PULL_DEADLINE_SECONDS:-0}" -gt 0 ]]; then
				remaining=$((HFL_DOCKER_PULL_DEADLINE_SECONDS - SECONDS))
				if [[ "${remaining}" -le 0 ]]; then
					HFL_DOCKER_LAST_ERROR="Docker pull budget exhausted after ${image} attempt ${attempt}"
					rm -f "${log_file}"
					return 1
				fi
				[[ "${remaining}" -ge "${sleep_seconds}" ]] || sleep_seconds=${remaining}
			fi
			printf 'WARN: %s; retrying in %ss\n' "${HFL_DOCKER_LAST_ERROR}" "${sleep_seconds}" >&2
			sleep "${sleep_seconds}"
		fi
	done
	rm -f "${log_file}"
	return 1
}

hfl_docker_tag_local_alias() {
	local source=$1 alias=$2
	[[ "${source}" != "${alias}" ]] || return 0
	if ! docker tag "${source}" "${alias}"; then
		HFL_DOCKER_LAST_ERROR="unable to tag ${source} as ${alias}"
		return 1
	fi
}

hfl_docker_ensure_image() {
	local image=$1 mirror="${2:-}" force_pull=${3:-0} offline=${4:-0}
	local platform="${5:-}" timeout_seconds=${6:-180} retries=${7:-2}
	local mirror_host mirrored="" local_alias="${image%@*}"
	HFL_DOCKER_IMAGE_SOURCE=""
	HFL_DOCKER_LAST_ERROR=""

	mirror_host="$(hfl_docker_normalize_mirror_host "${mirror}")"
	[[ -z "${mirror_host}" ]] || mirrored="$(hfl_docker_mirror_image_ref "${image}" "${mirror_host}")"

	if [[ "${force_pull}" -eq 0 ]] && hfl_docker_image_matches_platform "${image}" "${platform}"; then
		hfl_docker_tag_local_alias "${image}" "${local_alias}" || return 1
		HFL_DOCKER_IMAGE_SOURCE="local"
		return 0
	fi
	if [[ "${force_pull}" -eq 0 && -n "${mirrored}" ]] \
		&& hfl_docker_image_matches_platform "${mirrored}" "${platform}"; then
		hfl_docker_tag_local_alias "${mirrored}" "${local_alias}" || return 1
		HFL_DOCKER_IMAGE_SOURCE="local-mirror"
		return 0
	fi

	if [[ "${offline}" -eq 1 ]]; then
		if hfl_docker_image_matches_platform "${image}" "${platform}"; then
			hfl_docker_tag_local_alias "${image}" "${local_alias}" || return 1
			HFL_DOCKER_IMAGE_SOURCE="local-offline"
			return 0
		fi
		if [[ -n "${mirrored}" ]] && hfl_docker_image_matches_platform "${mirrored}" "${platform}"; then
			hfl_docker_tag_local_alias "${mirrored}" "${local_alias}" || return 1
			HFL_DOCKER_IMAGE_SOURCE="local-mirror-offline"
			return 0
		fi
		HFL_DOCKER_LAST_ERROR="${image} is missing and offline mode forbids registry access"
		return 1
	fi

	if [[ -n "${mirrored}" ]] \
		&& hfl_docker_pull_with_retry "${mirrored}" "${platform}" "${timeout_seconds}" "${retries}"; then
		hfl_docker_tag_local_alias "${mirrored}" "${local_alias}" || return 1
		HFL_DOCKER_IMAGE_SOURCE="mirror"
		return 0
	fi
	if hfl_docker_pull_with_retry "${image}" "${platform}" "${timeout_seconds}" "${retries}"; then
		hfl_docker_tag_local_alias "${image}" "${local_alias}" || return 1
		HFL_DOCKER_IMAGE_SOURCE="official"
		return 0
	fi

	if hfl_docker_image_matches_platform "${image}" "${platform}"; then
		hfl_docker_tag_local_alias "${image}" "${local_alias}" || return 1
		HFL_DOCKER_IMAGE_SOURCE="local-fallback"
		return 0
	fi
	if [[ -n "${mirrored}" ]] && hfl_docker_image_matches_platform "${mirrored}" "${platform}"; then
		hfl_docker_tag_local_alias "${mirrored}" "${local_alias}" || return 1
		HFL_DOCKER_IMAGE_SOURCE="local-mirror-fallback"
		return 0
	fi
	HFL_DOCKER_LAST_ERROR="unable to resolve ${image} from local cache, mirror, or official registry"
	return 1
}
