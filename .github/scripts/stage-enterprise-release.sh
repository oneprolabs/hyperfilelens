#!/usr/bin/env bash
# Upload an Enterprise candidate to its temporary Draft Release, then ask the
# TEST host to download it through the configured target-side proxy.
set -euo pipefail

source_dir=${1:-}
incoming=${2:-}
asset_list="$(dirname "${source_dir}")/release-assets.txt"

[[ -d "${source_dir}" && ! -L "${source_dir}" ]] || {
	printf 'ERROR: Enterprise release source directory is invalid\n' >&2
	exit 2
}
[[ "${incoming}" =~ ^/root/hfl-release/\.incoming/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
	printf 'ERROR: Enterprise incoming directory is invalid\n' >&2
	exit 2
}
[[ "${ARTIFACT_ID:-}" =~ ^enterprise-v[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-[0-9]+$ ]] || {
	printf 'ERROR: Enterprise candidate identity is invalid\n' >&2
	exit 2
}
[[ "${GITHUB_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
	printf 'ERROR: GitHub repository identity is invalid\n' >&2
	exit 2
}
[[ -s "${asset_list}" ]] || { printf 'ERROR: release asset list is missing\n' >&2; exit 2; }
[[ "${TEST_RELEASE_DOWNLOAD_PROXY_URL:-}" =~ ^https?://[A-Za-z0-9.-]+:[0-9]{1,5}$ ]] || {
	printf 'ERROR: TEST release download proxy is invalid\n' >&2
	exit 2
}
proxy_port=${TEST_RELEASE_DOWNLOAD_PROXY_URL##*:}
((proxy_port >= 1 && proxy_port <= 65535)) || {
	printf 'ERROR: TEST release download proxy port is out of range\n' >&2
	exit 2
}
[[ "${TEST_SSH_HOST:-}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ \
	&& "${TEST_SSH_PORT:-}" =~ ^[0-9]+$ ]] || {
	printf 'ERROR: invalid TEST SSH endpoint\n' >&2
	exit 2
}
[[ "${TEST_SSH_USER:-}" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
	printf 'ERROR: invalid TEST SSH user\n' >&2
	exit 2
}
[[ -n "${TEST_SSH_KNOWN_HOSTS:-}" && -n "${TEST_SSH_PRIVATE_KEY:-}" \
	&& -n "${GH_TOKEN:-}" ]] || {
	printf 'ERROR: Enterprise staging credentials are missing\n' >&2
	exit 2
}

(
	cd "${source_dir}"
	[[ -s SHA256SUMS && -s MANIFEST.json ]] || {
		printf 'ERROR: Enterprise release metadata is incomplete\n' >&2
		exit 1
	}
	sha256sum -c SHA256SUMS
	archive_count="$(find . -mindepth 1 -maxdepth 1 -type f \
		-name 'hyperfilelens-*-ee.tar.gz' -print | wc -l)"
	[[ "${archive_count}" -eq 1 ]] || {
		printf 'ERROR: Enterprise release must contain one canonical archive\n' >&2
		exit 1
	}
)

while IFS= read -r asset; do
	[[ "${asset}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ \
		&& -f "${source_dir}/${asset}" ]] || {
		printf 'ERROR: unsafe or missing Enterprise release asset: %s\n' "${asset}" >&2
		exit 1
	}
	./release/ci/gh-release-upload.sh "${ARTIFACT_ID}" \
		"${source_dir}/${asset}" --clobber
done <"${asset_list}"

assets_json="$(mktemp)"
plan="$(mktemp)"
trap 'rm -f "${assets_json}" "${plan}"' EXIT
gh release view "${ARTIFACT_ID}" --repo "${GITHUB_REPOSITORY}" \
	--json assets >"${assets_json}"

while IFS= read -r asset; do
	api_url="$(python3 - "${assets_json}" "${asset}" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
matches = [item["apiUrl"] for item in payload["assets"] if item["name"] == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit(f"temporary Enterprise asset is missing or ambiguous: {sys.argv[2]}")
print(matches[0])
PY
)"
	signed_url="$(curl -fsSI \
		-H 'Accept: application/octet-stream' \
		-H "Authorization: Bearer ${GH_TOKEN}" \
		"${api_url}" \
		| awk 'BEGIN { IGNORECASE=1 } /^location:/ { sub(/\r$/, "", $2); print $2; exit }')"
	python3 - "${asset}" "${signed_url}" >>"${plan}" <<'PY'
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

asset, url = sys.argv[1:]
parts = urllib.parse.urlsplit(url)
if parts.scheme != "https" or parts.hostname != "release-assets.githubusercontent.com":
    raise SystemExit("GitHub returned an unexpected Enterprise asset download URL")
if not parts.query or any(character in url for character in "\r\n\t"):
    raise SystemExit("GitHub returned an invalid Enterprise asset download URL")
expires = urllib.parse.parse_qs(parts.query).get("se", [""])[0]
try:
    expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
except ValueError as error:
    raise SystemExit("GitHub asset download URL has no valid expiry") from error
if expires_at < datetime.now(timezone.utc) + timedelta(minutes=50):
    raise SystemExit("GitHub asset download URL expires too soon")
print(f"{asset}\t{url}")
PY
done <"${asset_list}"

plan_base64="$(base64 -w 0 "${plan}")"
install -d -m 0700 ~/.ssh
printf '%s\n' "${TEST_SSH_KNOWN_HOSTS}" >~/.ssh/known_hosts
printf '%s\n' "${TEST_SSH_PRIVATE_KEY}" >~/.ssh/hyperfilelens_test
chmod 0600 ~/.ssh/known_hosts ~/.ssh/hyperfilelens_test
printf -v remote_command '%q ' bash -s -- \
	"${incoming}" "${TEST_RELEASE_DOWNLOAD_PROXY_URL}" "${plan_base64}"
ssh -i ~/.ssh/hyperfilelens_test \
	-o BatchMode=yes -o StrictHostKeyChecking=yes \
	-o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
	-p "${TEST_SSH_PORT}" "${TEST_SSH_USER}@${TEST_SSH_HOST}" \
	"${remote_command}" <.github/scripts/download-enterprise-release.sh
