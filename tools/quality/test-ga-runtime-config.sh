#!/usr/bin/env bash
# Validate site-scoped, runtime-only Google Analytics configuration.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
renderer="${ROOT}/deploy/docker/frontend-runtime-config.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

website="${tmp}/website.js"
tenant="${tmp}/tenant.js"
admin="${tmp}/admin.js"
HFL_WEBSITE_CONFIG_OUTPUT="${website}" \
	HFL_TENANT_CONFIG_OUTPUT="${tenant}" \
	HFL_ADMIN_CONFIG_OUTPUT="${admin}" \
	HFL_WEBSITE_APP_URL="https://app.hyperfilelens.com" \
	HFL_GA_MEASUREMENT_ID="G-0RX9GZJCWF" \
	sh "${renderer}"

grep -Fx "window.__HFL_WEBSITE_CONFIG__ = Object.freeze({ appUrl: 'https://app.hyperfilelens.com', gaMeasurementId: 'G-0RX9GZJCWF' })" \
	"${website}" >/dev/null
grep -F "gaMeasurementId: 'G-0RX9GZJCWF'" "${tenant}" >/dev/null
grep -F "gaMeasurementId: ''" "${admin}" >/dev/null

invalid_output="$(HFL_WEBSITE_CONFIG_OUTPUT="${website}" \
	HFL_TENANT_CONFIG_OUTPUT="${tenant}" \
	HFL_ADMIN_CONFIG_OUTPUT="${admin}" \
	HFL_GA_MEASUREMENT_ID="G invalid" sh "${renderer}" 2>&1)"
grep -F 'WARNING: invalid GA4 measurement ID' <<<"${invalid_output}" >/dev/null
grep -F "gaMeasurementId: ''" "${tenant}" >/dev/null

grep -F 'alias /usr/share/nginx/runtime/tenant-app-runtime-config.js;' \
	"${ROOT}/deploy/nginx/web.conf" >/dev/null
grep -F 'alias /usr/share/nginx/runtime/admin-app-runtime-config.js;' \
	"${ROOT}/deploy/nginx/web.conf" >/dev/null
grep -F '<script src="/app-runtime-config.js"></script>' \
	"${ROOT}/src/frontend/index.html" >/dev/null
grep -F 'PROD_GA_MEASUREMENT_ID' \
	"${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'ga4_measurement_id_pattern.fullmatch(candidate)' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F "event_callback: completeNavigation" \
	"${ROOT}/website/.vitepress/theme/analytics.ts" >/dev/null
grep -F 'window.setTimeout(completeNavigation, NAVIGATION_FALLBACK_MS)' \
	"${ROOT}/website/.vitepress/theme/analytics.ts" >/dev/null

python3 - "${ROOT}/.github/workflows/deploy_target.yml" "${tmp}" <<'PY'
import os
import pathlib
import subprocess
import sys
import textwrap

workflow = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
start_marker = '          python3 - "$runtime_env" <<\'PY\'\n'
end_marker = "\n          PY\n"
start = workflow.index(start_marker) + len(start_marker)
end = workflow.index(end_marker, start)
renderer = textwrap.dedent(workflow[start:end])


def render(target: str, measurement_id: str, filename: str):
    output = pathlib.Path(sys.argv[2], filename)
    environment = os.environ.copy()
    environment.update(
        DEPLOY_TARGET=target,
        HFL_GA_MEASUREMENT_ID=measurement_id,
    )
    completed = subprocess.run(
        [sys.executable, "-", str(output)],
        check=True,
        capture_output=True,
        env=environment,
        input=renderer,
        text=True,
    )
    values = dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
    )
    return values["HFL_GA_MEASUREMENT_ID"], completed.stdout


assert render("prod", "  G-0RX9GZJCWF\t", "trimmed.env") == ("G-0RX9GZJCWF", "")
invalid_value, invalid_output = render(
    "prod", "G-0RX9GZJCWF\ninvalid", "multiline.env"
)
assert invalid_value == ""
assert "::warning title=Google Analytics configuration::" in invalid_output
assert render("community", "G-0RX9GZJCWF", "community.env") == ("", "")
PY

if grep -R -E '(TEST|PREPROD)_GA_MEASUREMENT_ID' "${ROOT}/.github/workflows" >/dev/null; then
	printf 'ERROR: Google Analytics must be configured only for the PROD SaaS target\n' >&2
	exit 1
fi
if grep -R -F 'VITE_GA_ID' "${ROOT}/src/frontend" "${ROOT}/website" >/dev/null; then
	printf 'ERROR: HFL analytics must remain runtime-configured, not image-baked\n' >&2
	exit 1
fi

printf 'Runtime Google Analytics configuration checks passed.\n'
