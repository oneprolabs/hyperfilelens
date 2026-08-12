#!/usr/bin/env bash
# Runs on the TEST host. Download a private Draft Release through a short-lived
# GitHub asset URL without receiving repository credentials.
set -euo pipefail

incoming=${1:-}
download_proxy=${2:-}
plan_base64=${3:-}

[[ "${incoming}" =~ ^/root/hfl-release/\.incoming/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
	printf 'ERROR: Enterprise incoming directory is invalid\n' >&2
	exit 2
}
[[ "${download_proxy}" =~ ^https?://[A-Za-z0-9.-]+:[0-9]{1,5}$ ]] || {
	printf 'ERROR: Enterprise download proxy is invalid\n' >&2
	exit 2
}
proxy_port=${download_proxy##*:}
((proxy_port >= 1 && proxy_port <= 65535)) || {
	printf 'ERROR: Enterprise download proxy port is out of range\n' >&2
	exit 2
}
[[ -n "${plan_base64}" ]] || { printf 'ERROR: download plan is missing\n' >&2; exit 2; }

install -d -m 0700 "${incoming}"
plan="${incoming}/.download-plan"
trap 'rm -f "${plan}"' EXIT
printf '%s' "${plan_base64}" | base64 -d >"${plan}"
chmod 0600 "${plan}"

download() {
	local name=$1 url=$2 partial
	[[ "${name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || return 2
	[[ "${url}" == https://release-assets.githubusercontent.com/* ]] || return 2
	partial="${incoming}/.${name}.partial"
	printf '[enterprise-stage] Downloading %s through TEST proxy\n' "${name}"
	curl -fL --silent --show-error \
		--proxy "${download_proxy}" --noproxy '' \
		--connect-timeout 30 --max-time 3000 \
		--speed-time 180 --speed-limit 1024 \
		--retry 12 --retry-delay 5 --retry-all-errors \
		--continue-at - --output "${partial}" "${url}"
	mv "${partial}" "${incoming}/${name}"
}

while IFS=$'\t' read -r name url; do
	[[ -n "${name}" && -n "${url}" ]] || {
		printf 'ERROR: invalid Enterprise download plan\n' >&2
		exit 1
	}
	download "${name}" "${url}"
done <"${plan}"

(
	cd "${incoming}"
	sha256sum -c SHA256SUMS
)
