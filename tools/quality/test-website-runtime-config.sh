#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
renderer="${ROOT}/website/runtime-config.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

valid="${tmp}/valid.js"
HFL_WEBSITE_CONFIG_OUTPUT="${valid}" \
	HFL_WEBSITE_APP_URL="https://app.hyperfilelens.com" \
	HFL_GA_MEASUREMENT_ID="G-0RX9GZJCWF" \
	sh "${renderer}"
grep -Fx "window.__HFL_WEBSITE_CONFIG__ = Object.freeze({ appUrl: 'https://app.hyperfilelens.com', gaMeasurementId: 'G-0RX9GZJCWF' })" \
	"${valid}" >/dev/null

direct="${tmp}/direct.js"
output="$(HFL_WEBSITE_CONFIG_OUTPUT="${direct}" \
	HFL_WEBSITE_APP_URL="not a public origin" sh "${renderer}" 2>&1)"
grep -F 'WARNING: invalid app URL' <<<"${output}" >/dev/null
grep -Fx "window.__HFL_WEBSITE_CONFIG__ = Object.freeze({ appUrl: '', gaMeasurementId: '' })" \
	"${direct}" >/dev/null

invalid_ga="${tmp}/invalid-ga.js"
output="$(HFL_WEBSITE_CONFIG_OUTPUT="${invalid_ga}" \
	HFL_GA_MEASUREMENT_ID="invalid" sh "${renderer}" 2>&1)"
grep -F 'WARNING: invalid GA4 measurement ID' <<<"${output}" >/dev/null
grep -Fx "window.__HFL_WEBSITE_CONFIG__ = Object.freeze({ appUrl: '', gaMeasurementId: '' })" \
	"${invalid_ga}" >/dev/null

grep -F "directAppOrigin()" \
	"${ROOT}/website/.vitepress/theme/HomeLanding.vue" >/dev/null
if grep -R -F 'https://app.hyperfilelens.com/login' "${ROOT}/website" \
	--exclude-dir=node_modules --exclude-dir=.vitepress >/dev/null; then
	printf 'ERROR: Website login CTA must use runtime configuration\n' >&2
	exit 1
fi

for nginx_config in \
	"${ROOT}/deploy/nginx/web.conf" \
	"${ROOT}/deploy/nginx/development-web.conf"; do
	grep -F 'try_files $uri $uri/ $uri.html =404;' "${nginx_config}" >/dev/null
done

printf 'Website runtime URL configuration checks passed.\n'
