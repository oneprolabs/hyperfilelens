#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

po_file=${1:-}
[[ -f "${po_file}" ]] \
	|| lang_pack_die "usage: validate-backend-catalog.sh TRANSLATION.po"

backend_root="${HFL_BACKEND_SOURCE_ROOT:-${REPO_ROOT}/src/backend}"
[[ -d "${backend_root}" ]] || lang_pack_die "backend source root is missing: ${backend_root}"
lang_pack_require_command msgcmp
lang_pack_require_command xgettext

temporary_root="$(mktemp -d)"
trap 'rm -rf "${temporary_root}"' EXIT
source_catalog="${temporary_root}/backend.pot"
mapfile -d '' -t source_files < <(
	find "${backend_root}" -type f -name '*.py' \
		! -path '*/locale/*' \
		! -path '*/migrations/*' \
		! -path '*/tests/*' \
		-print0 | sort -z
)
((${#source_files[@]} > 0)) || lang_pack_die "backend source contains no Python files"

xgettext \
	--language=Python \
	--from-code=UTF-8 \
	--keyword=_ \
	--keyword=gettext_noop \
	--sort-output \
	--no-wrap \
	--output="${source_catalog}" \
	"${source_files[@]}"

if ! msgcmp --no-fuzzy-matching "${po_file}" "${source_catalog}"; then
	lang_pack_die "backend translation is incomplete or stale"
fi
