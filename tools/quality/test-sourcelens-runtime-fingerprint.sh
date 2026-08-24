#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck disable=SC1090
source "${ROOT_REPO}/deploy/installer/install.sh"

tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
ROOT="${tmp}/install"
SOURCELENS_INSTALL_DIR="${ROOT}/sourcelens"
target="${tmp}/target/sourcelens"
mkdir -p "${SOURCELENS_INSTALL_DIR}" "${ROOT}/data/sourcelens/config" "${target}"
printf 'services: {}\n' >"${SOURCELENS_INSTALL_DIR}/docker-compose.yml"
printf 'DJANGO_DEBUG=true\n' >"${ROOT}/data/sourcelens/config/.env"
ln -s "${ROOT}/data/sourcelens/config/.env" "${SOURCELENS_INSTALL_DIR}/.env"

write_bundle() {
	local root=$1 patchset=$2
	cat >"${root}/BUILD_INFO.json" <<JSON
{
  "git_url": "https://github.com/oneprolabs/sourcelens.git",
  "git_ref": "v0.20.0",
  "git_commit": "source-commit",
  "version": "0.20.0",
  "patchset_sha256": "${patchset}",
  "patches": [],
  "images": {
    "backend": {
      "ref": "hyperfilelens-sourcelens-backend:main-fixture-sl0.20.0"
    },
    "frontend": {
      "ref": "hyperfilelens-sourcelens-frontend:main-fixture-sl0.20.0"
    }
  }
}
JSON
	printf 'services: {}\n' >"${root}/docker-compose.yml"
	printf 'lifecycle-helper-v1\n' >"${root}/compose-lifecycle.sh"
}

write_bundle "${SOURCELENS_INSTALL_DIR}" old-patchset
write_bundle "${target}" old-patchset

runtime_version=0.4.0
sourcelens_compose() {
	if [[ "${1:-}" == "ps" && "${2:-}" == "--all" && "${3:-}" == "-q" ]]; then
		printf '%s-cid\n' "${4:-unknown}"
	fi
}
docker() {
	if [[ "${1:-}" == "inspect" ]]; then
		local container_id="${*: -1}" component=backend
		[[ "${container_id}" == web-cid ]] && component=frontend
		if [[ "$*" == *'{{.Config.Image}}'* ]]; then
			printf 'hyperfilelens-sourcelens-%s:main-fixture-sl%s\n' \
				"${component}" "${runtime_version}"
		else
			printf 'sha256:%064x\n' "$((10#${runtime_version//./} + ${#container_id}))"
		fi
		return 0
	fi
	return 1
}

# Target files alone are not proof of a successful upgrade. The real TEST
# failure left v0.20 files beside running v0.4 containers and must reconcile.
sourcelens_bundle_changed "${tmp}/target"
[[ ! -e "$(sourcelens_installed_fingerprint_path)" ]]

# A markerless runtime is reconciled once even when its version matches. Only
# an exact post-health runtime record can prove same-version patch identity.
runtime_version=0.20.0
sourcelens_bundle_changed "${tmp}/target"
record_sourcelens_installed_bundle "${target}"
if sourcelens_bundle_changed "${tmp}/target"; then
	printf 'ERROR: recorded SourceLens runtime was reported as changed\n' >&2
	exit 1
fi

# Runtime drift wins over a matching persistent marker.
runtime_version=0.4.0
sourcelens_bundle_changed "${tmp}/target"

# Semantic integration changes win even while the upstream version is stable.
runtime_version=0.20.0
write_bundle "${target}" new-patchset
sourcelens_bundle_changed "${tmp}/target"
record_sourcelens_installed_bundle "${target}"
if sourcelens_bundle_changed "${tmp}/target"; then
	printf 'ERROR: recorded SourceLens runtime was reported as changed\n' >&2
	exit 1
fi

# Lifecycle recovery is part of the installed runtime contract even when the
# upstream SourceLens version and image identities stay unchanged.
printf 'lifecycle-helper-v2\n' >"${target}/compose-lifecycle.sh"
sourcelens_bundle_changed "${tmp}/target"
record_sourcelens_installed_bundle "${target}"
if sourcelens_bundle_changed "${tmp}/target"; then
	printf 'ERROR: recorded SourceLens lifecycle helper was reported as changed\n' >&2
	exit 1
fi

printf 'SourceLens runtime fingerprint checks passed.\n'
