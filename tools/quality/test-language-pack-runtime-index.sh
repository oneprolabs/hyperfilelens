#!/usr/bin/env bash
set -euo pipefail

ROOT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# shellcheck source=../../deploy/installer/install.sh
source "${ROOT_REPO}/deploy/installer/install.sh"

ROOT="${tmp}/runtime"
mkdir -p "${ROOT}"
printf '%s\n' '1.0.0' >"${ROOT}/VERSION"
ensure_data_dirs
language_root="${ROOT}/data/lang-packs/versions/1.0.0"

python3 - "${language_root}/installed.json" <<'PY'
import json
import pathlib
import stat
import sys

index_path = pathlib.Path(sys.argv[1])
assert json.loads(index_path.read_text(encoding="utf-8")) == {
    "schema": 1,
    "app_version": "1.0.0",
    "packs": [],
}
assert stat.S_IMODE(index_path.stat().st_mode) == 0o644
PY

mkdir -p "${language_root}/fr"
cat >"${language_root}/fr/manifest.json" <<'JSON'
{
  "schema": 1,
  "id": "fr",
  "display_name": "French",
  "version": "1.0.0",
  "frontend_code": "fr",
  "backend_code": "fr",
  "aliases": ["fr-fr"]
}
JSON
printf '%s\n' '{"broken":true}' >"${language_root}/installed.json"
chmod 0600 "${language_root}/installed.json"

ensure_data_dirs

python3 - "${language_root}/installed.json" <<'PY'
import json
import pathlib
import stat
import sys

index_path = pathlib.Path(sys.argv[1])
assert json.loads(index_path.read_text(encoding="utf-8")) == {
    "schema": 1,
    "app_version": "1.0.0",
    "packs": [
        {
            "schema": 1,
            "id": "fr",
            "display_name": "French",
            "version": "1.0.0",
            "frontend_code": "fr",
            "backend_code": "fr",
            "aliases": ["fr-fr"],
        }
    ],
}
assert stat.S_IMODE(index_path.stat().st_mode) == 0o644
PY

for compose_file in docker-compose.yml deploy/docker-compose.yml; do
	grep -F 'https://127.0.0.1:11443/locales/installed.json' \
		"${ROOT_REPO}/${compose_file}" >/dev/null
	grep -F './data/lang-packs/versions/${HFL_PRODUCT_VERSION:-latest}' \
		"${ROOT_REPO}/${compose_file}" >/dev/null
done
grep -F '"app_version"' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null

printf 'Language-pack runtime index checks passed.\n'
