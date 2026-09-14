#!/bin/sh
set -eu

output=${HFL_WEBSITE_CONFIG_OUTPUT:-/usr/share/nginx/website/website-runtime-config.js}
temporary="${output}.tmp"
app_url=${HFL_WEBSITE_APP_URL:-}
website_ga_measurement_id=${HFL_WEBSITE_GA_MEASUREMENT_ID:-}

if [ -n "${app_url}" ] && ! printf '%s' "${app_url}" \
  | grep -Eq '^https?://(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(:[0-9]{1,5})?/?$'; then
  printf '%s\n' '[website-config] WARNING: invalid app URL; using the browser host and port 11443 fallback' >&2
  app_url=
fi

if [ -n "${website_ga_measurement_id}" ] && ! printf '%s' "${website_ga_measurement_id}" \
  | grep -Eq '^G-[A-Z0-9]+$'; then
  printf '%s\n' '[website-config] WARNING: invalid Website GA4 measurement ID; Website analytics is disabled' >&2
  website_ga_measurement_id=
fi

umask 022
printf "window.__HFL_WEBSITE_CONFIG__ = Object.freeze({ appUrl: '%s', gaMeasurementId: '%s' })\n" \
  "${app_url%/}" "${website_ga_measurement_id}" > "${temporary}"
mv -f "${temporary}" "${output}"
