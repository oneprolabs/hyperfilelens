#!/usr/bin/env bash
set -euo pipefail

LANG_PACK_TOOLING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANG_PACKS_ROOT="$(cd "${LANG_PACK_TOOLING_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${LANG_PACKS_ROOT}/.." && pwd)"
LANG_PACK_BUILD_ROOT="${HFL_LANG_PACK_BUILD_ROOT:-${REPO_ROOT}/build/language-packs}"

lang_pack_log() { printf '[language-packs] %s\n' "$*" >&2; }
lang_pack_die() { printf '[language-packs] ERROR: %s\n' "$*" >&2; exit 1; }

lang_pack_require_command() {
	command -v "$1" >/dev/null 2>&1 || lang_pack_die "missing required command: $1"
}

lang_pack_validate_version() {
	[[ "$1" =~ ^([0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9][A-Za-z0-9.-]*)?|main-[0-9a-f]{7})$ ]] \
		|| lang_pack_die "invalid application version: $1"
}

lang_pack_ids() {
	python3 - "${LANG_PACKS_ROOT}/catalog.json" <<'PY'
import json
import pathlib
import sys

catalog = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if catalog.get("schema") != 1:
    raise SystemExit("language-pack catalog schema must be 1")
for pack in catalog.get("packs", []):
    if pack.get("status") == "bundled":
        print(pack["id"])
PY
}

lang_pack_dir() {
	local pack_id=$1
	python3 - "${LANG_PACKS_ROOT}/catalog.json" "${pack_id}" "${LANG_PACKS_ROOT}" <<'PY'
import json
import pathlib
import sys

catalog_path = pathlib.Path(sys.argv[1])
pack_id = sys.argv[2]
root = pathlib.Path(sys.argv[3]).resolve()
catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
for pack in catalog.get("packs", []):
    if pack.get("id") != pack_id:
        continue
    path = (root / str(pack.get("path", ""))).resolve()
    if path.parent != (root / "packs").resolve():
        raise SystemExit(f"unsafe pack path for {pack_id}")
    print(path)
    raise SystemExit(0)
raise SystemExit(f"unknown language pack: {pack_id}")
PY
}
