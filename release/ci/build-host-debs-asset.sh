#!/usr/bin/env bash
# Fetch one supported Ubuntu Docker CE bundle for release assembly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

[[ $# -eq 2 ]] || {
	printf 'Usage: %s UBUNTU_RELEASE OUTPUT_TAR\n' "$0" >&2
	exit 2
}
ubuntu_release=$1
output=$2
case "${ubuntu_release}" in
20.04) release_id=2004 ;;
22.04) release_id=2204 ;;
24.04) release_id=2404 ;;
*) printf 'ERROR: unsupported Ubuntu release: %s\n' "${ubuntu_release}" >&2; exit 2 ;;
esac
"${ROOT}/tools/dependencies/fetch-docker-ce-debs.sh" --ubuntu-release "${ubuntu_release}"
source_dir="${ROOT}/build/dependencies/docker/ubuntu-${ubuntu_release}/amd64"
[[ -d "${source_dir}" ]] || { printf 'ERROR: Docker deb output is missing\n' >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
gateway_dir="${tmp}/payload/media/gateway-bootstrap"
mkdir -p "${gateway_dir}"
# Deterministic packaging: fixed entry order and zeroed mtimes so identical
# dependency sets produce byte-identical bundles (digest reuse across versions).
tar -C "${source_dir}" --sort=name --mtime='@0' -c . \
	| gzip -n > "${gateway_dir}/docker-debs-ubuntu${release_id}-amd64.tar.gz"
mkdir -p "$(dirname "${output}")"
tar -C "${tmp}" --sort=name --mtime='@0' -cf "${output}" payload
