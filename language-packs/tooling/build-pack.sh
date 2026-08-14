#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

pack_id=""
version=""
while [[ $# -gt 0 ]]; do
	case "$1" in
	--pack) shift; pack_id=${1:-} ;;
	--version) shift; version=${1:-} ;;
	*) lang_pack_die "unknown option: $1" ;;
	esac
	shift
done

[[ -n "${pack_id}" && -n "${version}" ]] \
	|| lang_pack_die "usage: build-pack.sh --pack ID --version X.Y.Z"
lang_pack_validate_version "${version}"
lang_pack_require_command node
lang_pack_require_command python3
lang_pack_require_command msgfmt
lang_pack_require_command msgcmp
lang_pack_require_command xgettext

pack_dir="$(lang_pack_dir "${pack_id}")"
stage="${LANG_PACK_BUILD_ROOT}/staging/${pack_id}"
dist="${LANG_PACK_BUILD_ROOT}/dist"
archive="${dist}/hyperfilelens-lang-${pack_id}-${version}.tar.gz"

rm -rf "${stage}"
mkdir -p "${stage}/frontend" "${stage}/backend/locale" "${dist}"

node "${SCRIPT_DIR}/build-frontend-pack.mjs" \
	"${pack_dir}" "${stage}" "${version}"

locale_dir="$(python3 - "${pack_dir}/definition.json" <<'PY'
import json
import pathlib
import sys

definition = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
language, separator, territory = definition["backend_code"].lower().partition("-")
if not separator:
    print(language)
else:
    normalized = territory.title() if len(territory) > 2 else territory.upper()
    print(f"{language}_{normalized}")
PY
)"
po_file="${pack_dir}/backend/locale/${locale_dir}/LC_MESSAGES/django.po"
mo_dir="${stage}/backend/locale/${locale_dir}/LC_MESSAGES"
[[ -f "${po_file}" ]] || lang_pack_die "missing backend translation: ${po_file}"
mkdir -p "${mo_dir}"
"${SCRIPT_DIR}/validate-backend-catalog.sh" "${po_file}"
msgfmt --check --check-format -o "${mo_dir}/django.mo" "${po_file}"
[[ -s "${mo_dir}/django.mo" ]] || lang_pack_die "compiled backend catalog is empty"

python3 "${SCRIPT_DIR}/validate-runtime-pack.py" "${stage}" "${version}"

tmp_archive="${archive}.part"
rm -f "${tmp_archive}"
COPYFILE_DISABLE=1 tar \
	--sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
	-C "${stage}" -czf "${tmp_archive}" .
mv "${tmp_archive}" "${archive}"
chmod 0644 "${archive}"
lang_pack_log "built ${archive}"
