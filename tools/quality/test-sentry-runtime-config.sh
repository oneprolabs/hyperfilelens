#!/usr/bin/env bash
# Validate runtime-only Sentry wiring for HFL and bundled SourceLens.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

tenant="${tmp}/tenant.js"
admin="${tmp}/admin.js"
website="${tmp}/website.js"
HFL_WEBSITE_CONFIG_OUTPUT="${website}" \
	HFL_TENANT_CONFIG_OUTPUT="${tenant}" \
	HFL_ADMIN_CONFIG_OUTPUT="${admin}" \
	HFL_SENTRY_ENABLED=true \
	HFL_SENTRY_DSN=https://public@sentry.example.com/42 \
	HFL_SENTRY_ENVIRONMENT=hfl-community \
	HFL_SENTRY_RELEASE=hyperfilelens-frontend@0.1.8 \
	HFL_SENTRY_TRACES_SAMPLE_RATE=0.1 \
	sh "${ROOT}/deploy/docker/frontend-runtime-config.sh"
grep -F "sentryEnabled: true" "${tenant}" >/dev/null
grep -F "sentryEnvironment: 'hfl-community'" "${tenant}" >/dev/null
grep -F "sentrySurface: 'tenant'" "${tenant}" >/dev/null
grep -F "sentrySurface: 'admin'" "${admin}" >/dev/null
if grep -F 'sentryDsn' "${website}" >/dev/null; then
	printf 'ERROR: Website runtime config must not receive an HFL application DSN\n' >&2
	exit 1
fi

invalid_output="$(HFL_WEBSITE_CONFIG_OUTPUT="${website}" \
	HFL_TENANT_CONFIG_OUTPUT="${tenant}" \
	HFL_ADMIN_CONFIG_OUTPUT="${admin}" \
	HFL_SENTRY_ENABLED=true \
	HFL_SENTRY_DSN=invalid \
	HFL_SENTRY_ENVIRONMENT=hfl-test \
	HFL_SENTRY_RELEASE=hyperfilelens-frontend@main-0123456 \
	sh "${ROOT}/deploy/docker/frontend-runtime-config.sh" 2>&1)"
grep -F 'WARNING: invalid Sentry frontend DSN' <<<"${invalid_output}" >/dev/null
grep -F 'sentryEnabled: false' "${tenant}" >/dev/null

password_output="$(HFL_WEBSITE_CONFIG_OUTPUT="${website}" \
	HFL_TENANT_CONFIG_OUTPUT="${tenant}" \
	HFL_ADMIN_CONFIG_OUTPUT="${admin}" \
	HFL_SENTRY_ENABLED=true \
	HFL_SENTRY_DSN=https://public:secret@sentry.example.com/42 \
	HFL_SENTRY_ENVIRONMENT=hfl-test \
	HFL_SENTRY_RELEASE=hyperfilelens-frontend@main-0123456 \
	sh "${ROOT}/deploy/docker/frontend-runtime-config.sh" 2>&1)"
grep -F 'WARNING: invalid Sentry frontend DSN' <<<"${password_output}" >/dev/null
grep -F 'sentryEnabled: false' "${tenant}" >/dev/null

env_file="${tmp}/hfl.env"
runtime_file="${tmp}/runtime.env"
cat >"${env_file}" <<'EOF'
SENTRY_ENABLED=false
SENTRY_BACKEND_DSN=
SENTRY_FRONTEND_DSN=
SENTRY_ENVIRONMENT=
SENTRY_TRACES_SAMPLE_RATE=0
HFL_DEPLOY_TARGET=
HFL_DEPLOYMENT_MODE=standalone
HFL_INSECURE_TLS=1
DJANGO_ALLOWED_HOSTS=localhost
CSRF_TRUSTED_ORIGINS=https://localhost:11443
CORS_ALLOWED_ORIGINS=https://localhost:11443
EOF
cat >"${runtime_file}" <<'EOF'
SENTRY_ENABLED=true
SENTRY_BACKEND_DSN=https://backend@sentry.example.com/41
SENTRY_FRONTEND_DSN=https://frontend@sentry.example.com/42
SENTRY_ENVIRONMENT=hfl-community
SENTRY_TRACES_SAMPLE_RATE=0.1
HFL_DEPLOY_TARGET=community
HFL_INSECURE_TLS=1
HFL_PLATFORM_GATEWAY_AUTO_DEPLOY=false
HFL_EMAIL_SIGNUP_ENABLED=false
HFL_GOOGLE_OAUTH_ENABLED=false
HFL_GA_MEASUREMENT_ID=
TURNSTILE_ENABLED=false
TURNSTILE_SITE_KEY=
TURNSTILE_SECRET_KEY=
SMTP_HOST=
SMTP_PORT=
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_SECURITY=
EMAIL_FROM=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
EOF
chmod 600 "${env_file}" "${runtime_file}"
python3 "${ROOT}/deploy/installer/apply-runtime-config.py" \
	--env-file "${env_file}" \
	--direct-host 127.0.0.1 \
	--public-url https://127.0.0.1:11443 \
	--runtime-env-file "${runtime_file}" >/dev/null
grep -Fx 'SENTRY_ENABLED=true' "${env_file}" >/dev/null
grep -Fx 'SENTRY_BACKEND_DSN="https://backend@sentry.example.com/41"' "${env_file}" >/dev/null
grep -Fx 'SENTRY_FRONTEND_DSN="https://frontend@sentry.example.com/42"' "${env_file}" >/dev/null
grep -Fx 'SENTRY_ENVIRONMENT=hfl-community' "${env_file}" >/dev/null
grep -Fx 'HFL_DEPLOY_TARGET=community' "${env_file}" >/dev/null
grep -Fx 'HFL_DEPLOYMENT_MODE=managed' "${env_file}" >/dev/null

sl_env="${tmp}/sl.env"
cat >"${sl_env}" <<'EOF'
DJANGO_DEBUG=false
SENTRY_ENABLED=true
SENTRY_DSN=https://backend@sentry.example.com/41
EOF
python3 "${ROOT}/deploy/installer/sourcelens/patch-env-runtime.py" "${sl_env}"
grep -Fx 'SENTRY_ENABLED=true' "${sl_env}" >/dev/null
grep -Fx 'SENTRY_DSN=https://backend@sentry.example.com/41' "${sl_env}" >/dev/null
grep -Fx 'SENTRY_SEND_DEFAULT_PII=false' "${sl_env}" >/dev/null
printf '%s\n' '{"version":"0.20.0"}' >"${tmp}/BUILD_INFO.json"
python3 "${ROOT}/deploy/installer/sourcelens/sync-sentry-runtime.py" \
	--parent-env "${env_file}" \
	--sourcelens-env "${sl_env}" \
	--build-info "${tmp}/BUILD_INFO.json" \
	--frontend-config "${tmp}/sl-sentry-config.js"
grep -Fx 'SENTRY_RELEASE="hyperfilelens-sourcelens@unknown-sl0.20.0"' "${sl_env}" >/dev/null
grep -F '"enabled":true' "${tmp}/sl-sentry-config.js" >/dev/null
grep -F 'hyperfilelens-sourcelens-frontend@unknown-sl0.20.0' "${tmp}/sl-sentry-config.js" >/dev/null
[[ "$(stat -c '%a' "${sl_env}")" == "600" ]]
[[ "$(stat -c '%a' "${tmp}/sl-sentry-config.js")" == "644" ]]
python3 - "${ROOT}/deploy/installer/apply-runtime-config.py" \
	"${ROOT}/deploy/installer/sourcelens/sync-sentry-runtime.py" <<'PY'
import importlib.util
import pathlib
import sys


def load(path_value, name):
    path = pathlib.Path(path_value)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


legacy = "https://public:secret@sentry.example.com/42"
apply_runtime = load(sys.argv[1], "hfl_apply_runtime_config_test")
sync_runtime = load(sys.argv[2], "hfl_sync_sentry_runtime_test")
assert apply_runtime.valid_sentry_dsn(legacy)
assert not apply_runtime.valid_frontend_sentry_dsn(legacy)
assert sync_runtime.valid_dsn(legacy)
assert not sync_runtime.valid_frontend_dsn(legacy)
PY

sl_nginx="${tmp}/sourcelens-nginx.conf"
cat >"${sl_nginx}" <<'EOF'
server {
    listen 443 ssl;
    ssl_certificate /etc/nginx/certs/nginx-selfsigned.crt;
    ssl_certificate_key /etc/nginx/certs/nginx-selfsigned.key;
    set $ui_upstream http://frontend:80;
    # Frontend proxy to frontend container
    location / {
        proxy_pass $ui_upstream;
    }
}
EOF
# shellcheck source=../sourcelens/common.sh
source "${ROOT}/tools/sourcelens/common.sh"
sourcelens_patch_runtime_nginx "${sl_nginx}"
grep -F 'location = /hfl-sentry-config.js' "${sl_nginx}" >/dev/null
grep -F 'sub_filter_once on;' "${sl_nginx}" >/dev/null
grep -F 'hfl-sentry-loader.js' "${sl_nginx}" >/dev/null
grep -F 'https://js.sentry-cdn.com/' \
	"${ROOT}/deploy/installer/sourcelens/hfl-sentry-loader.js" >/dev/null
grep -F 'hfl-sentry-sitecustomize.py:/opt/backend/sitecustomize.py:ro' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null
grep -F 'hfl-sentry-sitecustomize.py:/opt/hfl-sentry/sitecustomize.py:ro' \
	"${ROOT}/deploy/installer/sourcelens/docker-compose.template.yml" >/dev/null

python3 - "${ROOT}/deploy/installer/sourcelens/hfl-sentry-sitecustomize.py" <<'PY'
import importlib.util
import pathlib
import sys
import types

captured = {}
fake_sdk = types.ModuleType("sentry_sdk")


def fake_init(*args, **kwargs):
    captured.update(kwargs)


fake_sdk.init = fake_init
sys.modules["sentry_sdk"] = fake_sdk
path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("hfl_sentry_sitecustomize_test", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
fake_sdk.init(before_send=lambda event, hint: event, send_default_pii=True)
assert captured["send_default_pii"] is False
assert captured["include_local_variables"] is False
assert captured["max_request_body_size"] == "never"
event = captured["before_send"](
    {
        "message": "customer prompt",
        "extra": {"args": ["/customer/private"]},
        "request": {
            "url": "https://app.example.com/customer/private?token=secret",
            "env": {"REMOTE_ADDR": "192.0.2.15"},
        },
        "transaction": "/customer/private",
        "transaction_info": {"source": "url"},
        "spans": [
            {
                "trace_id": "trace-span-1",
                "span_id": "span-1",
                "op": "db.sql.query",
                "description": "SELECT * FROM private_customer_files",
                "data": {"db.statement": "/customer/private"},
            }
        ],
        "exception": {"values": [{"value": "customer filename"}]},
    },
    {},
)
assert "message" not in event
assert "extra" not in event
assert event["exception"]["values"][0]["value"] == "[Filtered]"
assert event["request"] == {"url": "https://app.example.com", "headers": {}}
assert "transaction" not in event
assert "transaction_info" not in event
assert event["spans"] == [
    {"trace_id": "trace-span-1", "span_id": "span-1", "op": "db.sql.query"}
]
assert "192.0.2.15" not in str(event)
assert "/customer/private" not in str(event)
PY

node - "${ROOT}/deploy/installer/sourcelens/hfl-sentry-loader.js" <<'JS'
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')

let options
global.window = {
  __HFL_SOURCELENS_SENTRY__: {
    enabled: true,
    dsn: 'https://public@sentry.example.com/42',
    environment: 'hfl-test',
    release: 'hyperfilelens-sourcelens-frontend@main-0123456-sl0.20.0',
    tracesSampleRate: 0.1,
  },
  location: { origin: 'https://app.example.com' },
}
global.document = {
  createElement: () => ({ dataset: {} }),
  head: { appendChild: () => undefined },
}
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'))
window.Sentry = { init: (value) => { options = value } }
window.sentryOnLoad()
const event = options.beforeSendTransaction({
  contexts: {
    customer: { value: 'private' },
    trace: {
      trace_id: 'root-trace-1',
      span_id: 'root-span-1',
      op: 'navigation',
      data: { customer: 'private' },
    },
  },
  spans: [{
    is_segment: true,
    segment_id: 'segment-1',
    trace_id: 'trace-span-1',
    span_id: 'span-1',
    op: 'http.client',
    description: 'https://app.example.com/customer/private?token=secret',
    data: { authorization: 'secret' },
  }],
})
assert.deepEqual(event.contexts, {
  trace: {
    trace_id: 'root-trace-1',
    span_id: 'root-span-1',
    op: 'navigation',
    data: {},
  },
})
assert.deepEqual(event.spans, [{
  data: {},
  is_segment: true,
  segment_id: 'segment-1',
  trace_id: 'trace-span-1',
  span_id: 'span-1',
  op: 'http.client',
}])
assert(!JSON.stringify(event).includes('private'))
assert(!JSON.stringify(event).includes('secret'))
JS

if grep -R -n -E 'ARG SENTRY_DSN|ENV SENTRY_DSN|import\.meta\.env\.SENTRY' \
	"${ROOT}/deploy/docker/frontend.Dockerfile" \
	"${ROOT}/src/frontend" >/dev/null; then
	printf 'ERROR: HFL browser DSN must remain runtime-only\n' >&2
	exit 1
fi
grep -F 'TEST_SENTRY_ENABLED' "${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F 'COMMUNITY_SENTRY_ENABLED' "${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F 'PROD_SENTRY_ENABLED' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'SENTRY_AUTH_TOKEN' "${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
grep -F "secrets.SENTRY_AUTH_TOKEN != ''" \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null
if grep -F 'sentry_auth_token=${{ secrets.SENTRY_AUTH_TOKEN }}' \
	"${ROOT}/.github/workflows/release_pipeline.yml" >/dev/null; then
	printf 'ERROR: an empty Sentry token can still be passed as a BuildKit secret\n' >&2
	exit 1
fi

sourcemap_notice="$(env -u SENTRY_AUTH_TOKEN -u SENTRY_ORG -u SENTRY_FRONTEND_PROJECT \
	SENTRY_URL=https://sentry.example.com \
	"${ROOT}/release/ci/upload-sourcelens-sourcemaps.sh" test-release)"
grep -F '::notice title=Sentry Source Maps::Bundled SourceLens Source Map upload is skipped' \
	<<<"${sourcemap_notice}" >/dev/null
if grep -F '::warning' <<<"${sourcemap_notice}" >/dev/null; then
	printf 'ERROR: incomplete optional Source Map settings emitted a warning\n' >&2
	exit 1
fi
grep -F '::notice title=Sentry deployment::Deployment markers are skipped' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null

printf 'Runtime Sentry configuration checks passed.\n'
