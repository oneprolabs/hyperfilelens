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
	grep -F 'sh /etc/nginx/snippets/check-language-packs.sh' \
		"${ROOT_REPO}/${compose_file}" >/dev/null
	grep -F './data/lang-packs/versions/${HFL_PRODUCT_VERSION:-latest}' \
		"${ROOT_REPO}/${compose_file}" >/dev/null
done
health_root="${tmp}/health-root"
mock_bin="${tmp}/mock-bin"
wget_log="${tmp}/wget.log"
mkdir -p \
	"${health_root}/fr/frontend" \
	"${health_root}/zh-hans/frontend" \
	"${mock_bin}"
printf '%s\n' '{}' >"${health_root}/installed.json"
printf '%s\n' '{}' >"${health_root}/fr/frontend/messages.json"
printf '%s\n' '{}' >"${health_root}/zh-hans/frontend/messages.json"
printf '%s\n' '{}' >"${health_root}/zh-hans/frontend/element-plus.json"
cat >"${mock_bin}/wget" <<'SH'
#!/bin/sh
for argument do
	url="${argument}"
done
printf '%s\n' "${url}" >>"${HFL_TEST_WGET_LOG}"
if [ "${HFL_TEST_WGET_FAIL_URL:-}" = "${url}" ]; then
	exit 1
fi
SH
chmod +x "${mock_bin}/wget"
empty_health_root="${tmp}/empty-health-root"
empty_wget_log="${tmp}/empty-wget.log"
mkdir -p "${empty_health_root}"
printf '%s\n' '{}' >"${empty_health_root}/installed.json"
HFL_LANGUAGE_PACK_HEALTH_ROOT="${empty_health_root}" \
	HFL_LANGUAGE_PACK_HEALTH_BASE_URL=https://language-pack.test \
	HFL_TEST_WGET_LOG="${empty_wget_log}" \
	PATH="${mock_bin}:${PATH}" \
	sh "${ROOT_REPO}/deploy/nginx/snippets/check-language-packs.sh"
[[ "$(wc -l <"${empty_wget_log}")" -eq 1 ]]
grep -Fx 'https://language-pack.test/locales/installed.json' \
	"${empty_wget_log}" >/dev/null
HFL_LANGUAGE_PACK_HEALTH_ROOT="${health_root}" \
	HFL_LANGUAGE_PACK_HEALTH_BASE_URL=https://language-pack.test \
	HFL_TEST_WGET_LOG="${wget_log}" \
	PATH="${mock_bin}:${PATH}" \
	sh "${ROOT_REPO}/deploy/nginx/snippets/check-language-packs.sh"
grep -Fx 'https://language-pack.test/locales/installed.json' "${wget_log}" >/dev/null
grep -Fx 'https://language-pack.test/locales/fr/frontend/messages.json' \
	"${wget_log}" >/dev/null
if grep -Fx 'https://language-pack.test/locales/fr/frontend/element-plus.json' \
	"${wget_log}" >/dev/null; then
	printf 'ERROR: language-pack health check required an absent optional component catalog\n' >&2
	exit 1
fi
grep -Fx 'https://language-pack.test/locales/zh-hans/frontend/messages.json' \
	"${wget_log}" >/dev/null
grep -Fx 'https://language-pack.test/locales/zh-hans/frontend/element-plus.json' \
	"${wget_log}" >/dev/null
if HFL_LANGUAGE_PACK_HEALTH_ROOT="${health_root}" \
	HFL_LANGUAGE_PACK_HEALTH_BASE_URL=https://language-pack.test \
	HFL_TEST_WGET_LOG="${wget_log}" \
	HFL_TEST_WGET_FAIL_URL=https://language-pack.test/locales/zh-hans/frontend/messages.json \
	PATH="${mock_bin}:${PATH}" \
	sh "${ROOT_REPO}/deploy/nginx/snippets/check-language-packs.sh" >/dev/null 2>&1; then
	printf 'ERROR: language-pack health check ignored an unreadable message catalog\n' >&2
	exit 1
fi
grep -F '"app_version"' \
	"${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F '! -perm 0755' "${ROOT_REPO}/dev/stack.sh" >/dev/null
grep -F '! -perm 0644' "${ROOT_REPO}/dev/stack.sh" >/dev/null

printf 'Language-pack runtime index checks passed.\n'
