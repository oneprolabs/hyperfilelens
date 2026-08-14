#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

version=""
while [[ $# -gt 0 ]]; do
	case "$1" in
	--version)
		shift
		version=${1:-}
		[[ -n "${version}" ]] || lang_pack_die "--version requires a value"
		;;
	*) lang_pack_die "unknown option: $1" ;;
	esac
	shift
done

[[ -n "${version}" ]] || lang_pack_die "usage: build-all.sh --version X.Y.Z"
lang_pack_validate_version "${version}"

mapfile -t pack_ids < <(lang_pack_ids)
((${#pack_ids[@]} > 0)) || lang_pack_die "catalog has no bundled language packs"
for pack_id in "${pack_ids[@]}"; do
	[[ "${pack_id}" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] \
		|| lang_pack_die "catalog contains an invalid pack id: ${pack_id}"
done
mkdir -p "${LANG_PACK_BUILD_ROOT}/dist"
find "${LANG_PACK_BUILD_ROOT}/dist" -maxdepth 1 -type f \
	\( -name 'hyperfilelens-lang-*.tar.gz' -o -name 'hyperfilelens-lang-*.tar.gz.part' \) \
	-delete
for pack_id in "${pack_ids[@]}"; do
	"${SCRIPT_DIR}/build-pack.sh" --pack "${pack_id}" --version "${version}"
done
python3 "${SCRIPT_DIR}/validate-runtime-collection.py" \
	"${LANG_PACK_BUILD_ROOT}/staging" "${pack_ids[@]}"
