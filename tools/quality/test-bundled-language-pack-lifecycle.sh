#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "${test_root}"' EXIT

# shellcheck source=../../deploy/installer/install.sh
source "${REPO_ROOT}/deploy/installer/install.sh"

ROOT="${test_root}/runtime"
mkdir -p "${ROOT}/payload/language-packs"
printf '%s\n' '1.2.3' >"${ROOT}/VERSION"

# Releases before version-scoped catalogs stored installed packs directly below
# data/lang-packs. Preserve a compatible pack once when adopting the new layout.
legacy_fr="${ROOT}/data/lang-packs/fr"
mkdir -p \
	"${legacy_fr}/frontend" \
	"${legacy_fr}/backend/locale/fr/LC_MESSAGES"
printf '%s\n' \
	'{"schema":1,"id":"fr","display_name":"French","version":"1.0.0","compatible_app":">=1.0.0,<2.0.0","frontend_code":"fr","backend_code":"fr","aliases":["fr-fr"]}' \
	>"${legacy_fr}/manifest.json"
printf '%s\n' '{}' >"${legacy_fr}/frontend/messages.json"
printf '%s\n' 'compiled-catalog' \
	>"${legacy_fr}/backend/locale/fr/LC_MESSAGES/django.mo"

python3 - "${ROOT}/payload/language-packs/hyperfilelens-lang-zh-hans-1.2.3.tar.gz" <<'PY'
import io
import json
import pathlib
import tarfile
import sys

archive = pathlib.Path(sys.argv[1])
files = {
    "manifest.json": json.dumps(
        {
            "schema": 2,
            "id": "zh-hans",
            "display_name": "Simplified Chinese",
            "version": "1.2.3",
            "compatible_app": "==1.2.3",
            "frontend_code": "zh-hans",
            "backend_code": "zh-hans",
            "aliases": ["zh", "zh-cn"],
            "component_locale": "zh-cn",
        }
    ).encode(),
    "frontend/messages.json": b"{}\n",
    "frontend/element-plus.json": b"{}\n",
    "backend/locale/zh_Hans/LC_MESSAGES/django.mo": b"compiled-catalog",
}
with tarfile.open(archive, "w:gz") as package:
    for name, content in files.items():
        member = tarfile.TarInfo(name)
        member.size = len(content)
        # Runtime permissions must not trust archive metadata or caller umask.
        member.mode = 0o600
        package.addfile(member, io.BytesIO(content))
PY

original_umask="$(umask)"
umask 0077
ensure_data_dirs
sync_bundled_language_packs
umask "${original_umask}"
language_base="${ROOT}/data/lang-packs"
language_root="${language_base}/versions/1.2.3"
[[ -f "${language_root}/zh-hans/manifest.json" ]]
[[ -f "${language_root}/fr/manifest.json" ]]
[[ -f "${language_root}/.legacy-flat-layout-reviewed" ]]

python3 - "${language_root}/zh-hans" <<'PY'
import pathlib
import stat
import sys

pack_root = pathlib.Path(sys.argv[1])
for path in [pack_root, *pack_root.rglob("*")]:
    mode = stat.S_IMODE(path.stat().st_mode)
    if path.is_dir():
        assert mode == 0o755, (path, oct(mode))
    elif path.is_file():
        assert mode == 0o644, (path, oct(mode))
PY

mv "${language_root}/zh-hans" "${language_root}/.backup-zh-hans-100"
mkdir -p "${language_root}/.incoming-zh-hans-100"
activate_language_pack_archive \
	"${ROOT}/payload/language-packs/hyperfilelens-lang-zh-hans-1.2.3.tar.gz" \
	1.2.3 "${language_root}"
[[ -f "${language_root}/zh-hans/manifest.json" ]]
[[ ! -e "${language_root}/.backup-zh-hans-100" ]]
[[ ! -e "${language_root}/.incoming-zh-hans-100" ]]

update_language_pack_state disable zh-hans "${language_base}"
mkdir -p "${language_root}/.collection-backup-disabled"
mv "${language_root}/zh-hans" \
	"${language_root}/.collection-backup-disabled/zh-hans"
sync_bundled_language_packs
[[ ! -e "${language_root}/zh-hans" ]]
[[ ! -e "${language_root}/.collection-backup-disabled" ]]

update_language_pack_state enable zh-hans "${language_base}"
sync_bundled_language_packs
[[ -f "${language_root}/zh-hans/manifest.json" ]]

# State queries have three distinct outcomes. In particular, malformed state
# must stop lifecycle operations instead of silently enabling a bundled pack.
state_fixture="${test_root}/state-contract"
update_language_pack_state initialize "" "${state_fixture}"
state_status=0
update_language_pack_state is-disabled zh-hans "${state_fixture}" \
	|| state_status=$?
[[ "${state_status}" -eq 1 ]]
update_language_pack_state disable zh-hans "${state_fixture}"
update_language_pack_state is-disabled zh-hans "${state_fixture}"
printf '%s\n' '{broken json' >"${state_fixture}/.state.json"
state_status=0
update_language_pack_state is-disabled zh-hans "${state_fixture}" \
	2>/dev/null || state_status=$?
[[ "${state_status}" -eq 2 ]]

# Validate every bundled pack before mutating the installed collection. A later
# invalid archive must not leave an earlier pack from the same sync half-applied.
ATOMIC_ZH_ARCHIVE="${ROOT}/payload/language-packs/hyperfilelens-lang-zh-hans-1.2.3.tar.gz" \
	ATOMIC_INVALID_ARCHIVE="${ROOT}/payload/language-packs/hyperfilelens-lang-zz-1.2.3.tar.gz" \
	python3 - <<'PY'
from __future__ import annotations

import io
import json
import os
import pathlib
import tarfile


def write_archive(path: pathlib.Path, pack_id: str, messages: bytes, valid: bool) -> None:
    language, separator, territory = pack_id.partition("-")
    locale = language if not separator else f"{language}_{territory.title()}"
    files = {
        "manifest.json": json.dumps(
            {
                "schema": 2,
                "id": pack_id,
                "display_name": pack_id,
                "version": "1.2.3",
                "compatible_app": "==1.2.3",
                "frontend_code": pack_id,
                "backend_code": pack_id,
                "aliases": [],
                "component_locale": "zh-cn" if pack_id == "zh-hans" else pack_id,
            }
        ).encode(),
        "frontend/messages.json": messages,
        f"backend/locale/{locale}/LC_MESSAGES/django.mo": b"compiled-catalog",
    }
    if valid:
        files["frontend/element-plus.json"] = b"{}\n"
    with tarfile.open(path, "w:gz") as package:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            package.addfile(member, io.BytesIO(content))


write_archive(
    pathlib.Path(os.environ["ATOMIC_ZH_ARCHIVE"]),
    "zh-hans",
    b'{"revision":2}\n',
    True,
)
write_archive(
    pathlib.Path(os.environ["ATOMIC_INVALID_ARCHIVE"]),
    "zz",
    b"{}\n",
    False,
)
PY
if (sync_bundled_language_packs) >/dev/null 2>&1; then
	printf 'ERROR: bundled collection accepted an invalid later archive\n' >&2
	exit 1
fi
jq -e 'length == 0' "${language_root}/zh-hans/frontend/messages.json" >/dev/null
rm -f "${ROOT}/payload/language-packs/hyperfilelens-lang-zz-1.2.3.tar.gz"
sync_bundled_language_packs
jq -e '.revision == 2' "${language_root}/zh-hans/frontend/messages.json" >/dev/null

# A permission-normalization failure must stop before the current package is
# replaced. This also guards explicit error propagation when the activation
# function is invoked from an if-condition, where Bash errexit is suppressed.
permission_mock_bin="${test_root}/permission-mock-bin"
mkdir -p "${permission_mock_bin}"
cat >"${permission_mock_bin}/chmod" <<'SH'
#!/bin/sh
if [ "${1:-}" = "0755" ]; then
	exit 1
fi
exec /bin/chmod "$@"
SH
chmod +x "${permission_mock_bin}/chmod"
before_permission_failure_hash="$(
	sha256sum "${language_root}/zh-hans/frontend/messages.json"
)"
if (
	PATH="${permission_mock_bin}:${PATH}" activate_language_pack_archive \
		"${ROOT}/payload/language-packs/hyperfilelens-lang-zh-hans-1.2.3.tar.gz" \
		1.2.3 "${language_root}"
) >/dev/null 2>&1; then
	printf 'ERROR: language pack activation ignored a permission failure\n' >&2
	exit 1
fi
[[ "$(sha256sum "${language_root}/zh-hans/frontend/messages.json")" \
	== "${before_permission_failure_hash}" ]]
if find "${language_root}" -mindepth 1 -maxdepth 1 \
	\( -name '.extract-*' -o -name '.incoming-*' \) -print -quit | grep -q .; then
	printf 'ERROR: permission failure left language-pack activation residue\n' >&2
	exit 1
fi

# Simulate termination after the installed package was moved to the collection
# backup but before its replacement was promoted. The next sync must restore a
# usable package, apply the current Release archive, and remove all residue.
mkdir -p \
	"${language_root}/.collection-interrupted/zh-hans" \
	"${language_root}/.collection-backup-interrupted"
cp -a "${language_root}/zh-hans/." \
	"${language_root}/.collection-interrupted/zh-hans/"
mv "${language_root}/zh-hans" \
	"${language_root}/.collection-backup-interrupted/zh-hans"
sync_bundled_language_packs
jq -e '.revision == 2' "${language_root}/zh-hans/frontend/messages.json" >/dev/null
if find "${language_root}" -mindepth 1 -maxdepth 1 -type d \
	\( -name '.collection-*' -o -name '.collection-backup-*' \) \
	-print -quit | grep -q .; then
	printf 'ERROR: interrupted collection transaction residue was not cleaned\n' >&2
	exit 1
fi

# The collection sync also converges residue from an interrupted independent
# install before it snapshots the installed set.
mv "${language_root}/zh-hans" "${language_root}/.backup-zh-hans-4242"
mkdir -p "${language_root}/.incoming-zh-hans-4242"
sync_bundled_language_packs
jq -e '.revision == 2' "${language_root}/zh-hans/frontend/messages.json" >/dev/null
[[ ! -e "${language_root}/.backup-zh-hans-4242" ]]
[[ ! -e "${language_root}/.incoming-zh-hans-4242" ]]

# A corrupt persistent state file must fail before the installed collection is
# staged, recovered, or replaced.
cp "${language_base}/.state.json" "${test_root}/valid-language-state.json"
before_catalog_hash="$(sha256sum "${language_root}/zh-hans/frontend/messages.json")"
printf '%s\n' '{broken json' >"${language_base}/.state.json"
sync_status=0
(sync_bundled_language_packs) >/dev/null 2>&1 || sync_status=$?
[[ "${sync_status}" -ne 0 ]]
[[ "$(sha256sum "${language_root}/zh-hans/frontend/messages.json")" == "${before_catalog_hash}" ]]
if find "${language_root}" -mindepth 1 -maxdepth 1 -type d \
	-name '.collection-*' -print -quit | grep -q .; then
	printf 'ERROR: corrupt state allowed a collection transaction to start\n' >&2
	exit 1
fi
mv "${test_root}/valid-language-state.json" "${language_base}/.state.json"

INVALID_VERSION_ARCHIVE="${test_root}/hyperfilelens-lang-fr-9.9.9.tar.gz" \
	CONFLICT_ARCHIVE="${test_root}/hyperfilelens-lang-zh-alt-1.2.3.tar.gz" \
	RESERVED_ALIAS_ARCHIVE="${test_root}/hyperfilelens-lang-fr-1.2.3.tar.gz" python3 - <<'PY'
from __future__ import annotations

import io
import json
import os
import pathlib
import tarfile


def write_pack(
    archive: pathlib.Path,
    *,
    pack_id: str,
    version: str,
    compatible_app: str,
    frontend_code: str,
    backend_code: str,
    aliases: list[str],
) -> None:
    language, separator, territory = backend_code.lower().partition("-")
    locale = language
    if separator:
        normalized = territory.title() if len(territory) > 2 else territory.upper()
        locale = f"{language}_{normalized}"
    files = {
        "manifest.json": json.dumps(
            {
                "schema": 2,
                "id": pack_id,
                "display_name": pack_id,
                "version": version,
                "compatible_app": compatible_app,
                "frontend_code": frontend_code,
                "backend_code": backend_code,
                "aliases": aliases,
                "component_locale": frontend_code,
            }
        ).encode(),
        "frontend/messages.json": b"{}\n",
        "frontend/element-plus.json": b"{}\n",
        f"backend/locale/{locale}/LC_MESSAGES/django.mo": b"compiled-catalog",
    }
    with tarfile.open(archive, "w:gz") as package:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            package.addfile(member, io.BytesIO(content))


write_pack(
    pathlib.Path(os.environ["INVALID_VERSION_ARCHIVE"]),
    pack_id="fr",
    version="9.9.9",
    compatible_app=">=1.0.0,<2.0.0",
    frontend_code="fr",
    backend_code="fr",
    aliases=["fr-fr"],
)
write_pack(
    pathlib.Path(os.environ["CONFLICT_ARCHIVE"]),
    pack_id="zh-alt",
    version="1.2.3",
    compatible_app="==1.2.3",
    frontend_code="zh-hans",
    backend_code="zh-hans",
    aliases=["zh", "zh-cn"],
)
write_pack(
    pathlib.Path(os.environ["RESERVED_ALIAS_ARCHIVE"]),
    pack_id="fr",
    version="1.2.3",
    compatible_app="==1.2.3",
    frontend_code="fr",
    backend_code="fr",
    aliases=["en"],
)
PY

if validate_and_extract_language_pack \
	"${test_root}/hyperfilelens-lang-fr-9.9.9.tar.gz" \
	"${test_root}/invalid-version" 1.2.3 >/dev/null 2>&1; then
	printf 'ERROR: schema 2 accepted a non-matching package version\n' >&2
	exit 1
fi
if validate_and_extract_language_pack \
	"${test_root}/hyperfilelens-lang-fr-1.2.3.tar.gz" \
	"${test_root}/reserved-alias" 1.2.3 >/dev/null 2>&1; then
	printf 'ERROR: installer accepted the built-in English locale as an alias\n' >&2
	exit 1
fi
if (
	activate_language_pack_archive \
		"${test_root}/hyperfilelens-lang-zh-alt-1.2.3.tar.gz" \
		1.2.3 "${language_root}"
) >/dev/null 2>&1; then
	printf 'ERROR: installer accepted conflicting language codes\n' >&2
	exit 1
fi
[[ ! -e "${language_root}/zh-alt" ]]

python3 - "${language_base}/.state.json" "${language_root}/installed.json" <<'PY'
import json
import pathlib
import sys

state = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
index = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert state == {"schema": 2, "disabled_packs": []}
assert [pack["id"] for pack in index["packs"]] == ["fr", "zh-hans"]
assert index["packs"][1]["schema"] == 2
assert index["packs"][1]["component_locale"] == "zh-cn"
PY

legacy_state_base="${test_root}/legacy-state"
mkdir -p "${legacy_state_base}"
printf '%s\n' '{"schema":1,"disabled_bundled":["fr"]}' \
	>"${legacy_state_base}/.state.json"
update_language_pack_state initialize "" "${legacy_state_base}"
jq -e '.schema == 2 and .disabled_packs == ["fr"] and has("disabled_bundled") == false' \
	"${legacy_state_base}/.state.json" >/dev/null

# A target version is isolated from the previous release so blue/green rollback
# can continue serving the previous version's catalogs.
mkdir -p "${language_base}/versions/1.2.2/legacy"
printf '%s\n' '{"version":"1.2.2"}' \
	>"${language_base}/versions/1.2.2/legacy/manifest.json"
sync_bundled_language_packs
[[ -f "${language_base}/versions/1.2.2/legacy/manifest.json" ]]
[[ -f "${language_root}/zh-hans/manifest.json" ]]

tooling_fixture="${test_root}/clean-build/language-packs/tooling"
build_fixture="${test_root}/clean-build/output"
mkdir -p "${tooling_fixture}" "${build_fixture}/dist"
cp "${REPO_ROOT}/language-packs/tooling/build-all.sh" \
	"${REPO_ROOT}/language-packs/tooling/lib.sh" \
	"${REPO_ROOT}/language-packs/tooling/validate-runtime-collection.py" \
	"${tooling_fixture}/"
cat >"${test_root}/clean-build/language-packs/catalog.json" <<'JSON'
{
  "schema": 1,
  "packs": [
    {"id": "zh-hans", "path": "packs/zh-hans", "status": "bundled"}
  ]
}
JSON
cat >"${tooling_fixture}/build-pack.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
pack_id=""
version=""
while [[ $# -gt 0 ]]; do
	case "$1" in
	--pack) pack_id=$2; shift 2 ;;
	--version) version=$2; shift 2 ;;
	*) exit 2 ;;
	esac
done
mkdir -p "${HFL_LANG_PACK_BUILD_ROOT}/staging/${pack_id}"
cat >"${HFL_LANG_PACK_BUILD_ROOT}/staging/${pack_id}/manifest.json" <<EOF
{"id":"${pack_id}","frontend_code":"zh-hans","backend_code":"zh-hans","aliases":["zh","zh-cn"]}
EOF
printf 'fixture\n' >"${HFL_LANG_PACK_BUILD_ROOT}/dist/hyperfilelens-lang-${pack_id}-${version}.tar.gz"
SH
chmod +x "${tooling_fixture}/build-all.sh" "${tooling_fixture}/build-pack.sh"
printf 'stale\n' >"${build_fixture}/dist/hyperfilelens-lang-retired-1.2.3.tar.gz"
HFL_LANG_PACK_BUILD_ROOT="${build_fixture}" \
	"${tooling_fixture}/build-all.sh" --version 1.2.3 >/dev/null
[[ ! -e "${build_fixture}/dist/hyperfilelens-lang-retired-1.2.3.tar.gz" ]]
[[ -f "${build_fixture}/dist/hyperfilelens-lang-zh-hans-1.2.3.tar.gz" ]]

collection_fixture="${test_root}/collection"
mkdir -p "${collection_fixture}/fr" "${collection_fixture}/fr-alt"
printf '%s\n' \
	'{"id":"fr","frontend_code":"fr","backend_code":"fr","aliases":["fr-fr"]}' \
	>"${collection_fixture}/fr/manifest.json"
printf '%s\n' \
	'{"id":"fr-alt","frontend_code":"fr-alt","backend_code":"fr-alt","aliases":["fr-fr"]}' \
	>"${collection_fixture}/fr-alt/manifest.json"
if python3 "${REPO_ROOT}/language-packs/tooling/validate-runtime-collection.py" \
	"${collection_fixture}" fr fr-alt >/dev/null 2>&1; then
	printf 'ERROR: build accepted conflicting language aliases\n' >&2
	exit 1
fi

printf 'Bundled language-pack lifecycle checks passed.\n'
