#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

digest="sha256:$(printf 'a%.0s' {1..64})"
"${ROOT}/release/ci/write-saas-image-metadata.sh" \
	hfl-backend \
	docker.io/example/hyperfilelens-backend:1.0.0-ee \
	registry.example.cn/example/hyperfilelens-backend:1.0.0-ee \
	"${digest}" \
	hyperfilelens-backend:1.0.0-ee \
	hyperfilelens \
	"${tmp}/metadata.json"

jq -e \
	--arg digest "${digest}" \
	'.digest == $digest
	 and .local_ref == "hyperfilelens-backend:1.0.0-ee"
	 and [.sources[].region] == ["cn", "global"]' \
	"${tmp}/metadata.json" >/dev/null

grep -Fq 'delivery_mode == "registry"' "${ROOT}/deploy/installer/install.sh"
grep -Fq -- '--runtime-only' "${ROOT}/release/build-sourcelens.sh"
grep -Fq 'group: hyperfilelens-deploy-test' \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
grep -Fq 'group: hyperfilelens-deploy-prod' \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
grep -Fq 'using: composite' "${ROOT}/.github/actions/deploy-saas/action.yml"
grep -Fq 'HFL_REGISTRY_REGION: ${{ vars.TEST_REGISTRY_REGION }}' \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
grep -Fq 'HFL_REGISTRY_REGION: ${{ vars.PROD_REGISTRY_REGION }}' \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
grep -Fq -- '--registry-region "$HFL_REGISTRY_REGION"' \
	"${ROOT}/.github/actions/deploy-saas/action.yml"
grep -Fq 'registry_login_count > 0' \
	"${ROOT}/.github/scripts/remote-saas-deploy.sh"
grep -Fq 'for prefix in "${registry_region}" "${fallback_region}"' \
	"${ROOT}/.github/scripts/remote-saas-deploy.sh"
grep -Fq 'platform-gateway ensure' \
	"${ROOT}/.github/actions/deploy-saas/action.yml"
grep -Fq 'reconcile-saas-ai-model.sh agent' \
	"${ROOT}/.github/actions/deploy-saas/action.yml"
grep -Fq "'HFL_AI_MODEL_APPLIED=false'" \
	"${ROOT}/.github/scripts/reconcile-saas-ai-model.sh"
grep -Fq 'TEST_AI_MULTIMODAL_MODEL_PROVIDER' \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
grep -Fq 'PROD_AI_MODEL_API_KEY' \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
grep -Fq 'Required repository secret is empty' \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
grep -Fq "registry delivery requires HFL_REGISTRY_REGION=cn or global" \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
for deploy_job in deploy-test deploy-prod; do
	job_definition="$(awk -v job="${deploy_job}" '
		$0 == "  " job ":" {inside = 1; next}
		inside && /^  [a-z0-9-]+:/ {exit}
		inside {print}
	' "${ROOT}/.github/workflows/enterprise_saas_upgrade.yml")"
	# shellcheck disable=SC2016 # GitHub expression is an intentional literal.
	grep -Fq 'ref: ${{ needs.prepare.outputs.commit }}' <<<"${job_definition}"
done
grep -Fq "format('hyperfilelens-package-{0}', github.event_name == 'push' && github.ref_name || inputs.tag)" \
	"${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"

package_root="${tmp}/candidate"
fake_bin="${tmp}/bin"
tag_marker="${tmp}/tagged"
pull_marker="${tmp}/pulls"
mirror_marker="${tmp}/mirror-attempts"
mirror_sleep_marker="${tmp}/mirror-sleeps"
mirror_inspect_marker="${tmp}/mirror-inspect-attempts"
mkdir -p "${package_root}" "${fake_bin}"
revision="$(printf 'b%.0s' {1..40})"
python3 - "${package_root}/MANIFEST.json" "${digest}" "${revision}" <<'PY'
import json, pathlib, sys
path, digest, revision = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    "git_commit": revision,
    "delivery": {
        "mode": "registry",
        "registry_images": [{
            "role": "hyperfilelens",
            "local_ref": "hyperfilelens-backend:1.0.0-ee",
            "digest": digest,
            "sources": [
                {"region": "cn", "ref": "registry.example.cn/example/hyperfilelens-backend:1.0.0-ee"},
                {"region": "global", "ref": "docker.io/example/hyperfilelens-backend:1.0.0-ee"},
            ],
        }],
    },
}), encoding="utf-8")
PY

cat >"${fake_bin}/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-} ${2:-}" in
"buildx imagetools")
	case "${3:-}" in
	create)
		[[ "$*" == *"${HFL_TEST_DIGEST}"* ]]
		count=0
		[[ ! -f "${HFL_TEST_MIRROR_MARKER}" ]] \
			|| count="$(cat "${HFL_TEST_MIRROR_MARKER}")"
		count=$((count + 1))
		printf '%s\n' "${count}" >"${HFL_TEST_MIRROR_MARKER}"
		case "${HFL_TEST_MIRROR_MODE:-success}" in
		flaky429 | always429)
			if [[ "${count}" -lt 3 ]]; then
				printf 'unexpected status from HEAD request: 429 Too Many Requests\n' >&2
				exit 1
			fi
			if [[ "${HFL_TEST_MIRROR_MODE}" == always429 ]]; then
				printf 'unexpected status from HEAD request: 429 Too Many Requests\n' >&2
				exit 1
			fi
			;;
		fatal403)
			printf 'unexpected status from HEAD request: 403 Forbidden\n' >&2
			exit 1
			;;
		success) ;;
		*) exit 2 ;;
		esac
		;;
	inspect)
		count=0
		[[ ! -f "${HFL_TEST_MIRROR_INSPECT_MARKER}" ]] \
			|| count="$(cat "${HFL_TEST_MIRROR_INSPECT_MARKER}")"
		count=$((count + 1))
		printf '%s\n' "${count}" >"${HFL_TEST_MIRROR_INSPECT_MARKER}"
		case "${HFL_TEST_MIRROR_INSPECT_MODE:-success}" in
		flaky429)
			if [[ "${count}" -lt 3 ]]; then
				printf 'unexpected status from HEAD request: 429 Too Many Requests\n' >&2
				exit 1
			fi
			;;
		fatal403)
			printf 'unexpected status from HEAD request: 403 Forbidden\n' >&2
			exit 1
			;;
		success) ;;
		*) exit 2 ;;
		esac
		printf '{"digest":"%s"}\n' "${HFL_TEST_DIGEST}"
		;;
	*) exit 2 ;;
	esac
	;;
"pull --platform")
	ref="${4:-}"
	printf '%s\n' "${ref}" >>"${HFL_TEST_PULL_MARKER}"
	case "${HFL_TEST_FAIL_REGION:-}" in
	cn) [[ "${ref}" == registry.example.cn/* ]] && exit 1 ;;
	global) [[ "${ref}" == docker.io/* ]] && exit 1 ;;
	both) exit 1 ;;
	"") ;;
	*) exit 2 ;;
	esac
	[[ "${ref}" == registry.example.cn/* || "${ref}" == docker.io/* ]]
	printf 'pulled %s\n' "${ref}"
	;;
"tag "*)
	[[ "${2:-}" == *@sha256:* ]]
	: >"${HFL_TEST_TAG_MARKER}"
	;;
"image inspect")
	[[ -f "${HFL_TEST_TAG_MARKER}" ]] || exit 1
	printf '[{"RepoDigests":["docker.io/example/hyperfilelens-backend@%s"],"Config":{"Labels":{"org.opencontainers.image.revision":"%s"}}}]\n' \
		"${HFL_TEST_DIGEST}" "${HFL_TEST_REVISION}"
	;;
*)
	printf 'unexpected fake docker invocation: %s\n' "$*" >&2
	exit 2
	;;
esac
SH
chmod 755 "${fake_bin}/docker"
cat >"${fake_bin}/sleep" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]]
printf '%s\n' "$1" >>"${HFL_TEST_MIRROR_SLEEP_MARKER}"
SH
chmod 755 "${fake_bin}/sleep"
cat >"${fake_bin}/tee" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
/usr/bin/tee "$@"
[[ "${HFL_TEST_MIRROR_TEE_FAIL:-0}" != 1 ]] || exit 9
SH
chmod 755 "${fake_bin}/tee"
cat >"${fake_bin}/ssh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cat >/dev/null
case "${HFL_TEST_AI_RESULT:-passed}" in
passed)
	printf 'HFL_AI_MODEL_APPLIED=true\nHFL_AI_MODEL_CONNECTIVITY=passed\n'
	;;
rejected)
	printf 'HFL_AI_MODEL_APPLIED=false\n'
	;;
command-failed)
	exit 1
	;;
*) exit 2 ;;
esac
SH
chmod 755 "${fake_bin}/ssh"
export HFL_TEST_TAG_MARKER="${tag_marker}"
export HFL_TEST_PULL_MARKER="${pull_marker}"
export HFL_TEST_MIRROR_MARKER="${mirror_marker}"
export HFL_TEST_MIRROR_SLEEP_MARKER="${mirror_sleep_marker}"
export HFL_TEST_MIRROR_INSPECT_MARKER="${mirror_inspect_marker}"
export HFL_TEST_REVISION="${revision}"
export HFL_TEST_DIGEST="${digest}"
export PATH="${fake_bin}:${PATH}"
HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
	docker.io/example/hyperfilelens-backend:1.0.0-ee \
	"${digest}" \
	registry.example.cn/example/hyperfilelens-backend:1.0.0-ee
[[ "$(cat "${mirror_marker}")" -eq 1 ]]
[[ "$(cat "${mirror_inspect_marker}")" -eq 1 ]]

printf '0\n' >"${mirror_marker}"
HFL_TEST_MIRROR_MODE=flaky429 \
	HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/example/hyperfilelens-backend:1.0.0-ee \
		"${digest}" \
		registry.example.cn/example/hyperfilelens-backend:1.0.0-ee
[[ "$(cat "${mirror_marker}")" -eq 3 ]]

printf '0\n' >"${mirror_marker}"
: >"${mirror_sleep_marker}"
if HFL_TEST_MIRROR_MODE=always429 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/example/hyperfilelens-backend:1.0.0-ee \
		"${digest}" \
		registry.example.cn/example/hyperfilelens-backend:1.0.0-ee \
		>/dev/null 2>&1; then
	printf 'ERROR: registry mirror accepted five consecutive rate-limit failures\n' >&2
	exit 1
fi
[[ "$(cat "${mirror_marker}")" -eq 5 ]]
[[ "$(paste -sd, "${mirror_sleep_marker}")" == 5,10,20,40 ]]

printf '0\n' >"${mirror_marker}"
if HFL_TEST_MIRROR_MODE=fatal403 \
	HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/example/hyperfilelens-backend:1.0.0-ee \
		"${digest}" \
		registry.example.cn/example/hyperfilelens-backend:1.0.0-ee \
		>/dev/null 2>&1; then
	printf 'ERROR: registry mirror retried a non-retryable authorization failure\n' >&2
	exit 1
fi
[[ "$(cat "${mirror_marker}")" -eq 1 ]]

printf '0\n' >"${mirror_marker}"
printf '0\n' >"${mirror_inspect_marker}"
HFL_TEST_MIRROR_INSPECT_MODE=flaky429 \
	HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/example/hyperfilelens-backend:1.0.0-ee \
		"${digest}" \
		registry.example.cn/example/hyperfilelens-backend:1.0.0-ee
[[ "$(cat "${mirror_marker}")" -eq 1 ]]
[[ "$(cat "${mirror_inspect_marker}")" -eq 3 ]]

printf '0\n' >"${mirror_marker}"
printf '0\n' >"${mirror_inspect_marker}"
if HFL_TEST_MIRROR_TEE_FAIL=1 \
	HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/example/hyperfilelens-backend:1.0.0-ee \
		"${digest}" \
		registry.example.cn/example/hyperfilelens-backend:1.0.0-ee \
		>/dev/null 2>&1; then
	printf 'ERROR: registry mirror ignored a logging pipeline failure\n' >&2
	exit 1
fi
[[ "$(cat "${mirror_marker}")" -eq 1 ]]
[[ "$(cat "${mirror_inspect_marker}")" -eq 0 ]]
source "${ROOT}/deploy/installer/install.sh"
HFL_TEST_FAIL_REGION=cn HFL_REGISTRY_REGION=cn \
	load_images_from_manifest 0 "${package_root}"
[[ -f "${tag_marker}" ]]
[[ "$(wc -l <"${pull_marker}")" -eq 2 ]]
[[ "$(sed -n '1p' "${pull_marker}")" == registry.example.cn/* ]]
[[ "$(sed -n '2p' "${pull_marker}")" == docker.io/* ]]
HFL_REGISTRY_REGION=cn load_images_from_manifest 0 "${package_root}"
[[ "$(wc -l <"${pull_marker}")" -eq 2 ]]
online_registry_output="$(
	HFL_ONLINE_CHILD=1 HFL_REGISTRY_REGION=cn \
		load_images_from_manifest 0 "${package_root}"
)"
grep -F '[....] Verifying prepared runtime image (1/1):' \
	<<<"${online_registry_output}" >/dev/null
grep -F '[ OK ] Runtime image 1/1 verified ·' \
	<<<"${online_registry_output}" >/dev/null
grep -F '[ OK ] All 1 prepared runtime images are verified' \
	<<<"${online_registry_output}" >/dev/null

rm -f "${tag_marker}"
: >"${pull_marker}"
HFL_REGISTRY_REGION=global load_images_from_manifest 0 "${package_root}"
[[ -f "${tag_marker}" ]]
[[ "$(wc -l <"${pull_marker}")" -eq 1 ]]
[[ "$(sed -n '1p' "${pull_marker}")" == docker.io/* ]]

rm -f "${tag_marker}"
: >"${pull_marker}"
HFL_TEST_FAIL_REGION=global HFL_REGISTRY_REGION=global \
	load_images_from_manifest 0 "${package_root}"
[[ -f "${tag_marker}" ]]
[[ "$(wc -l <"${pull_marker}")" -eq 2 ]]
[[ "$(sed -n '1p' "${pull_marker}")" == docker.io/* ]]
[[ "$(sed -n '2p' "${pull_marker}")" == registry.example.cn/* ]]

rm -f "${tag_marker}"
: >"${pull_marker}"
if HFL_TEST_FAIL_REGION=both HFL_REGISTRY_REGION=cn \
	load_images_from_manifest 0 "${package_root}" >/dev/null 2>&1; then
	printf 'ERROR: registry delivery accepted two unavailable sources\n' >&2
	exit 1
fi
[[ ! -e "${tag_marker}" ]]
[[ "$(wc -l <"${pull_marker}")" -eq 2 ]]

export DEPLOY_SSH_HOST=test.example.com
export DEPLOY_SSH_PORT=22
export DEPLOY_SSH_USER=root
export AI_MODEL_PROVIDER=openai
export AI_MODEL_ID=test-agent
export AI_MODEL_DISPLAY_NAME="Test Agent"
export AI_MULTIMODAL_MODEL_PROVIDER=openai
export AI_MULTIMODAL_MODEL_ID=test-vision
export AI_MULTIMODAL_MODEL_DISPLAY_NAME="Test Vision"
export AI_MODEL_API_BASE=https://example.com/v1
export AI_MODEL_API_KEY=test-secret
export GITHUB_STEP_SUMMARY="${tmp}/ai-summary.md"
export RUNNER_TEMP="${tmp}"
HFL_TEST_AI_RESULT=passed \
	"${ROOT}/.github/scripts/reconcile-saas-ai-model.sh" agent
grep -Fq 'Passed: the deployment-managed ai model was applied and verified.' \
	"${GITHUB_STEP_SUMMARY}"
if AI_MODEL_ID="" HFL_TEST_AI_RESULT=passed \
	"${ROOT}/.github/scripts/reconcile-saas-ai-model.sh" agent >/dev/null 2>&1; then
	printf 'ERROR: required Agent model accepted incomplete configuration\n' >&2
	exit 1
fi
AI_MULTIMODAL_MODEL_ID="" HFL_TEST_AI_RESULT=passed \
	"${ROOT}/.github/scripts/reconcile-saas-ai-model.sh" multimodal >/dev/null
HFL_TEST_AI_RESULT=rejected \
	"${ROOT}/.github/scripts/reconcile-saas-ai-model.sh" multimodal >/dev/null
if HFL_TEST_AI_RESULT=command-failed \
	"${ROOT}/.github/scripts/reconcile-saas-ai-model.sh" agent >/dev/null 2>&1; then
	printf 'ERROR: required Agent model ignored a reconciliation failure\n' >&2
	exit 1
fi

identity_root="${tmp}/registry-identity"
mkdir -p "${identity_root}"
printf '1.0.0\n' >"${identity_root}/VERSION"
python3 - "${identity_root}/MANIFEST.json" "${digest}" "${revision}" <<'PY'
import json, pathlib, sys
path, digest, revision = sys.argv[1:]
version = "1.0.0-ee"


def metadata(component, local_ref, role):
    repository = local_ref.rsplit(":", 1)[0]
    return {
        "component": component,
        "role": role,
        "local_ref": local_ref,
        "digest": digest,
        "sources": [
            {"region": "cn", "ref": f"registry.example.cn/example/{repository}:{version}"},
            {"region": "global", "ref": f"docker.io/example/{repository}:{version}"},
        ],
    }


runtime = [
    metadata("hfl-backend", "hyperfilelens-backend:1.0.0-ee", "hyperfilelens"),
    metadata("hfl-frontend", "hyperfilelens-frontend:1.0.0-ee", "hyperfilelens"),
]
assets = []
for kind in ("agent", "gateway", "language"):
    entry = metadata(
        f"{kind}-assets", f"hyperfilelens-{kind}-assets:1.0.0-ee", f"{kind}-assets"
    )
    entry["asset_kind"] = kind
    assets.append(entry)
manifest = {
    "channel": "release",
    "artifact_id": "v1.0.0",
    "version": "1.0.0",
    "image_version": "1.0.0-ee",
    "edition": "enterprise",
    "git_commit": revision,
    "extension_commit": "c" * 40,
    "runtime_images": {
        "backend": runtime[0]["local_ref"],
        "frontend": runtime[1]["local_ref"],
    },
    "images": [{
        "role": "hyperfilelens",
        "refs": [manifest_ref["local_ref"] for manifest_ref in runtime],
        "digests": [manifest_ref["digest"] for manifest_ref in runtime],
    }],
    "delivery": {"mode": "registry", "registry_images": runtime, "asset_images": assets},
}
pathlib.Path(path).write_text(json.dumps(manifest), encoding="utf-8")
PY
validate_package_identity "${identity_root}"
python3 - "${identity_root}/MANIFEST.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
manifest = json.loads(path.read_text(encoding="utf-8"))
manifest["delivery"]["registry_images"].pop()
path.write_text(json.dumps(manifest), encoding="utf-8")
PY
if (validate_package_identity "${identity_root}") >/dev/null 2>&1; then
	printf 'ERROR: registry identity accepted incomplete runtime metadata\n' >&2
	exit 1
fi
python3 - "${identity_root}/MANIFEST.json" "${digest}" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
manifest = json.loads(path.read_text(encoding="utf-8"))
manifest["delivery"]["registry_images"] = [{
    "component": "hfl-backend",
    "role": "hyperfilelens",
    "local_ref": "hyperfilelens-backend:1.0.0-ee",
    "digest": sys.argv[2],
    "sources": [
        {"region": "cn", "ref": "registry.example.cn/example/hyperfilelens-backend:1.0.0-ee"},
        {"region": "global", "ref": "docker.io/example/hyperfilelens-backend:1.0.0-ee"},
    ],
}, {
    "component": "hfl-frontend",
    "role": "hyperfilelens",
    "local_ref": "hyperfilelens-frontend:1.0.0-ee",
    "digest": sys.argv[2],
    "sources": [
        {"region": "cn", "ref": "registry.example.cn/example/hyperfilelens-frontend:1.0.0-ee"},
        {"region": "global", "ref": "docker.io/example/hyperfilelens-frontend:1.0.0-ee"},
    ],
}]
path.write_text(json.dumps(manifest), encoding="utf-8")
PY
validate_package_identity "${identity_root}"
python3 - "${identity_root}/MANIFEST.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
manifest = json.loads(path.read_text(encoding="utf-8"))
manifest["delivery"]["asset_images"].pop()
path.write_text(json.dumps(manifest), encoding="utf-8")
PY
if (validate_package_identity "${identity_root}") >/dev/null 2>&1; then
	printf 'ERROR: registry identity accepted an incomplete asset set\n' >&2
	exit 1
fi

sourcelens_root="${tmp}/sourcelens-candidate"
mkdir -p \
	"${sourcelens_root}/sourcelens/deploy/nginx/hfl-maintenance" \
	"${sourcelens_root}/sourcelens/deploy/sentry"
for relative in \
	sourcelens/BUILD_INFO.json \
	sourcelens/.env.example \
	sourcelens/compose-lifecycle.sh \
	sourcelens/docker-compose.yml \
	sourcelens/install.sh \
	sourcelens/patch-env-runtime.py \
	sourcelens/sync-sentry-runtime.py \
	sourcelens/deploy/nginx/default.conf \
	sourcelens/deploy/nginx/hfl-sentry-loader.js \
	sourcelens/deploy/nginx/hfl-maintenance/run-creation-gate.conf \
	sourcelens/deploy/sentry/hfl-sentry-sitecustomize.py; do
	: >"${sourcelens_root}/${relative}"
done
printf '%s\n' '{"delivery":{"mode":"registry"}}' \
	>"${sourcelens_root}/MANIFEST.json"
preflight_sourcelens_bundle "${sourcelens_root}"
printf '%s\n' '{"delivery":{"mode":"offline"}}' \
	>"${sourcelens_root}/MANIFEST.json"
if (preflight_sourcelens_bundle "${sourcelens_root}") >/dev/null 2>&1; then
	printf 'ERROR: offline SourceLens preflight accepted missing image archives\n' >&2
	exit 1
fi

printf 'SaaS registry delivery contract tests passed.\n'
