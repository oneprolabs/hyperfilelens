#!/usr/bin/env bash
# Guard local release packaging against unsafe filesystem deletions.
set -euo pipefail

safe_path_die() {
	printf '[safe-path] ERROR: %s\n' "$*" >&2
	exit 1
}

safe_normalize_dir() {
	local path=$1
	path="${path%/}"
	[[ -n "${path}" ]] || safe_path_die "empty path"
	printf '%s' "${path}"
}

safe_assert_absolute() {
	local path=$1 label=${2:-path}
	[[ "${path}" == /* ]] || safe_path_die "${label} must be an absolute path: ${path}"
	[[ "${path}" != *$'\n'* && "${path}" != *$'\r'* ]] \
		|| safe_path_die "${label} contains invalid characters"
	[[ "${path}" != *".."* ]] || safe_path_die "${label} must not contain '..': ${path}"
}

safe_assert_package_basename() {
	local name=$1
	[[ "${name}" =~ ^hyperfilelens-([0-9]+\.[0-9]+\.[0-9]+(-ee)?|main-[0-9a-f]{7})\.tar\.gz$ ]] \
		|| safe_path_die "invalid release package basename: ${name}"
	[[ "${name}" != */* ]] || safe_path_die "package basename must not contain slashes: ${name}"
}

safe_assert_staging_pkg_root() {
	local pkg_root=$1 staging_base=$2
	safe_assert_absolute "${pkg_root}" "staging package root"
	staging_base="$(safe_normalize_dir "${staging_base}")"
	pkg_root="$(safe_normalize_dir "${pkg_root}")"
	[[ "${pkg_root}" == "${staging_base}/"* ]] \
		|| safe_path_die "staging package root must be under ${staging_base}: ${pkg_root}"
	[[ "${pkg_root}" != "${staging_base}" ]] \
		|| safe_path_die "refusing to delete staging base directory"
	local rel="${pkg_root#"${staging_base}/"}"
	[[ "${rel}" =~ ^hyperfilelens-([0-9][0-9A-Za-z._-]*|main-[0-9a-f]{7})$ ]] \
		|| safe_path_die "unexpected staging directory name: ${rel}"
}

safe_rm_dir() {
	local dir=$1
	[[ -n "${dir}" && "${dir}" != "/" ]] \
		|| safe_path_die "refusing to remove unsafe directory path"
	rm -rf "${dir}"
}
