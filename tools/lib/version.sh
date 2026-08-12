#!/usr/bin/env bash
# Resolve release versions and Git commits for local packaging workflows.
set -euo pipefail

DEFAULT_RELEASE_VERSION="0.1.0"
VERSION_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

version_die() {
	local message=$1
	local code=${2:-1}
	printf '[version] ERROR: %s\n' "${message}" >&2
	exit "${code}"
}

normalize_release_version() {
	local version="${1#v}"
	[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
		|| version_die "invalid release version: ${1} (expected X.Y.Z)" 2
	printf '%s' "${version}"
}

normalize_edition() {
	local edition="${1:-community}"
	[[ "${edition}" == "community" || "${edition}" == "enterprise" ]] \
		|| version_die "invalid edition: ${edition} (expected community or enterprise)" 2
	printf '%s' "${edition}"
}

lowercase() {
	printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

normalize_main_artifact_id() {
	local artifact_id
	artifact_id="$(lowercase "$1")"
	[[ "${artifact_id}" =~ ^main-[0-9a-f]{7}$ ]] \
		|| version_die "invalid main build identifier: ${1} (expected main-<7 lowercase hex>)" 2
	printf '%s' "${artifact_id}"
}

normalize_artifact_id() {
	local value="${1:-}"
	if [[ "$(lowercase "${value}")" == main-* ]]; then
		normalize_main_artifact_id "${value}"
		return
	fi
	normalize_release_version "${value}"
}

artifact_channel() {
	local artifact_id
	artifact_id="$(normalize_artifact_id "$1")" || return $?
	if [[ "${artifact_id}" == main-* ]]; then
		printf 'main'
	else
		printf 'release'
	fi
}

resolve_release_version() {
	local requested="${RELEASE_VERSION:-}" tag_version="" version=""
	if command -v git >/dev/null 2>&1 \
		&& git -C "${VERSION_REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		tag_version="$(git -C "${VERSION_REPO_ROOT}" describe --tags --exact-match \
			--match 'v[0-9]*' HEAD 2>/dev/null || true)"
	fi
	if [[ -n "${tag_version}" ]]; then
		tag_version="$(normalize_release_version "${tag_version}")" || return $?
	fi
	if [[ -n "${requested}" ]]; then
		requested="$(normalize_release_version "${requested}")" || return $?
	fi
	if [[ -n "${tag_version}" && -n "${requested}" && "${tag_version}" != "${requested}" ]]; then
		version_die "requested version ${requested} does not match HEAD tag ${tag_version}" 2
	fi
	version="${tag_version:-${requested:-${DEFAULT_RELEASE_VERSION}}}"
	normalize_release_version "${version}" || return $?
}

resolve_commit_full() {
	local root="${1:-}"
	if [[ -n "${root}" ]] && command -v git >/dev/null 2>&1 \
		&& git -C "${root}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		git -C "${root}" rev-parse HEAD
		return 0
	fi
	if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		git rev-parse HEAD
		return 0
	fi
	version_die "cannot resolve git commit; run inside a Git repository"
}

resolve_commit7() {
	local full commit7
	full="$(resolve_commit_full "${1:-}")"
	commit7="$(lowercase "${full}")"
	[[ "${commit7}" =~ ^[0-9a-f]{7,40}$ ]] || version_die "invalid git commit: ${full}"
	printf '%.7s' "${commit7}"
}

release_package_basename_for_version() {
	local version commit7 edition
	version="$(normalize_artifact_id "$1")" || return $?
	commit7="$(lowercase "$2")"
	edition="$(normalize_edition "${3:-community}")" || return $?
	[[ "${commit7}" =~ ^[0-9a-f]{7}$ ]] \
		|| version_die "invalid short git commit: ${2} (expected 7 hexadecimal characters)" 2
	if [[ "${version}" == main-* ]]; then
		[[ "${version#main-}" == "${commit7}" ]] \
			|| version_die "main build identifier ${version} does not match commit ${commit7}" 2
		printf 'hyperfilelens-%s.tar.gz' "${version}"
		return
	fi
	if [[ "${edition}" == "enterprise" ]]; then
		printf 'hyperfilelens-%s-ee.tar.gz' "${version}"
	else
		printf 'hyperfilelens-%s.tar.gz' "${version}"
	fi
}
