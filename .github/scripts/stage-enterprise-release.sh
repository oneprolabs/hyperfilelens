#!/usr/bin/env bash
# After Runner verification succeeds, ask the TEST host to download the
# Enterprise candidate from its temporary Draft Release through the configured
# target-side proxy. Repository credentials never leave the Runner.
set -euo pipefail

incoming=${1:-}

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
assets_json="$(mktemp)"
plan="$(mktemp)"
trap 'rm -f "${assets_json}" "${plan}"' EXIT
gh release view "${ARTIFACT_ID}" --repo "${GITHUB_REPOSITORY}" \
	--json assets >"${assets_json}"

mapfile -t assets < <(python3 - "${assets_json}" <<'PY'
import json
import pathlib
import re
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
names = sorted(item["name"] for item in payload["assets"] if not item["name"].startswith("_internal-"))
archives = [name for name in names if re.fullmatch(r"hyperfilelens-[0-9]+\.[0-9]+\.[0-9]+-ee\.tar\.gz", name)]
required = {"SHA256SUMS", "MANIFEST.json"}
if len(archives) != 1 or not required.issubset(names):
    raise SystemExit("temporary Enterprise candidate is incomplete or ambiguous")
for name in names:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise SystemExit(f"unsafe Enterprise release asset name: {name}")
for name in names:
    if name not in archives:
        print(name)
for name in archives:
    print(name)
PY
)
(( ${#assets[@]} >= 3 )) || { printf 'ERROR: Enterprise asset list is empty\n' >&2; exit 1; }

install -d -m 0700 ~/.ssh
printf '%s\n' "${TEST_SSH_KNOWN_HOSTS}" >~/.ssh/known_hosts
printf '%s\n' "${TEST_SSH_PRIVATE_KEY}" >~/.ssh/hyperfilelens_test
chmod 0600 ~/.ssh/known_hosts ~/.ssh/hyperfilelens_test

run_remote() {
	local mode=$1 encoded_plan=${2:-} remote_command
	printf -v remote_command '%q ' bash -s -- \
		"${incoming}" "${TEST_RELEASE_DOWNLOAD_PROXY_URL}" "${encoded_plan}" "${mode}"
	ssh -i ~/.ssh/hyperfilelens_test \
		-o BatchMode=yes -o StrictHostKeyChecking=yes \
		-o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
		-p "${TEST_SSH_PORT}" "${TEST_SSH_USER}@${TEST_SSH_HOST}" \
		"${remote_command}" <.github/scripts/download-enterprise-release.sh
}

for asset in "${assets[@]}"; do
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
	asset_staged=false
	for attempt in 1 2 3; do
		signed_url="$(curl -fsSI \
			-H 'Accept: application/octet-stream' \
			-H "Authorization: Bearer ${GH_TOKEN}" \
			"${api_url}" \
			| awk 'BEGIN { IGNORECASE=1 } /^location:/ { sub(/\r$/, "", $2); print $2; exit }')"
		python3 - "${asset}" "${signed_url}" >"${plan}" <<'PY'
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
		plan_base64="$(base64 -w 0 "${plan}")"
		if run_remote download "${plan_base64}"; then
			asset_staged=true
			break
		fi
		printf 'WARN: Enterprise download attempt %d/3 failed for %s; refreshing URL\n' \
			"${attempt}" "${asset}" >&2
	done
	[[ "${asset_staged}" == "true" ]] || {
		printf 'ERROR: Enterprise asset download failed after URL refresh: %s\n' \
			"${asset}" >&2
		exit 1
	}
done

run_remote verify
