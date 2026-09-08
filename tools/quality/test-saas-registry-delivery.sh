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
grep -Fq 'pull_registry_image "${immutable_ref}" "${attempts}"' \
	"${ROOT}/.github/scripts/remote-saas-deploy.sh"
grep -Fq 'HFL_REGISTRY_PULL_RETRY_DELAY_SECONDS:-15' \
	"${ROOT}/.github/scripts/remote-saas-deploy.sh"
grep -Fq 'for prefix in "${registry_region}" "${fallback_region}"' \
	"${ROOT}/.github/scripts/remote-saas-deploy.sh"
grep -Fq 'Enterprise SaaS deployment action:' \
	"${ROOT}/.github/scripts/remote-saas-deploy.sh"
grep -Fq 'deployment_args+=(--from "${candidate_root}")' \
	"${ROOT}/.github/scripts/remote-saas-deploy.sh"
grep -Fq -- '--expected-tag "$EXPECTED_TAG"' \
	"${ROOT}/.github/actions/deploy-saas/action.yml"
grep -Fq 'candidate does not match the requested release tag' \
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

deployment_state_function="$(awk '
	/^resolve_deployment_action\(\) \{/ { capture = 1 }
	capture { print }
	capture && /^}$/ { exit }
' "${ROOT}/.github/scripts/remote-saas-deploy.sh")"
eval "${deployment_state_function}"
state_root="${tmp}/deployment-state"
[[ "$(resolve_deployment_action "${state_root}")" == "install" ]]
mkdir -p "${state_root}"
[[ "$(resolve_deployment_action "${state_root}")" == "install" ]]
touch "${state_root}/unexpected"
if resolve_deployment_action "${state_root}" >"${tmp}/state.out" 2>"${tmp}/state.err"; then
	printf 'ERROR: SaaS deployment accepted an installation root with unknown state\n' >&2
	exit 1
fi
grep -Fq 'installation root contains unrecognized state' "${tmp}/state.err"
rm -f "${state_root}/unexpected"
touch "${state_root}/.env" "${state_root}/VERSION" "${state_root}/MANIFEST.json"
[[ "$(resolve_deployment_action "${state_root}")" == "upgrade" ]]
rm -f "${state_root}/MANIFEST.json"
if resolve_deployment_action "${state_root}" >"${tmp}/state.out" 2>"${tmp}/state.err"; then
	printf 'ERROR: SaaS deployment accepted an incomplete installation identity\n' >&2
	exit 1
fi
grep -Fq 'incomplete HyperFileLens installation' "${tmp}/state.err"
touch "${state_root}/MANIFEST.json"
rm -f "${state_root}/VERSION"
ln -s /etc/os-release "${state_root}/VERSION"
if resolve_deployment_action "${state_root}" >"${tmp}/state.out" 2>"${tmp}/state.err"; then
	printf 'ERROR: SaaS deployment accepted a symbolic-link installation identity\n' >&2
	exit 1
fi
grep -Fq 'unsafe HyperFileLens installation identity file' "${tmp}/state.err"
rm -rf "${state_root}"
ln -s "${tmp}" "${state_root}"
if resolve_deployment_action "${state_root}" >"${tmp}/state.out" 2>"${tmp}/state.err"; then
	printf 'ERROR: SaaS deployment accepted a symbolic-link installation root\n' >&2
	exit 1
fi
grep -Fq 'unsafe HyperFileLens installation root' "${tmp}/state.err"

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
		flaky429 | always429 | flakyStream)
			if [[ "${count}" -lt 3 ]]; then
				if [[ "${HFL_TEST_MIRROR_MODE}" == "flakyStream" ]]; then
					printf 'failed to copy: stream error: stream ID 1; INTERNAL_ERROR; received from peer\n' >&2
				else
					printf 'unexpected status from HEAD request: 429 Too Many Requests\n' >&2
				fi
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
		missing-once)
			if [[ "${count}" -eq 1 ]]; then
				printf 'manifest unknown: manifest unknown\n' >&2
				exit 1
			fi
			;;
		mismatch)
			printf '{"digest":"sha256:%s"}\n' "$(printf 'f%.0s' {1..64})"
			exit 0
			;;
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
	transient_match=0
	case "${HFL_TEST_TRANSIENT_REGION:-}" in
	cn) [[ "${ref}" == registry.example.cn/* ]] && transient_match=1 ;;
	global) [[ "${ref}" == docker.io/* ]] && transient_match=1 ;;
	"") ;;
	*) exit 2 ;;
	esac
	if ((transient_match == 1)); then
		count=0
		[[ ! -f "${HFL_TEST_TRANSIENT_MARKER}" ]] \
			|| count="$(cat "${HFL_TEST_TRANSIENT_MARKER}")"
		count=$((count + 1))
		printf '%s\n' "${count}" >"${HFL_TEST_TRANSIENT_MARKER}"
		if ((count <= ${HFL_TEST_TRANSIENT_FAILURES:-1})); then
			printf 'short read: unexpected EOF\n' >&2
			exit 1
		fi
	fi
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
printf '0\n' >"${mirror_inspect_marker}"
HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/library/postgres:17 \
		"${digest}" \
		docker.io/example/postgres:17-aaaaaaaaaaaa \
		--if-missing
[[ "$(cat "${mirror_marker}")" -eq 0 ]]
[[ "$(cat "${mirror_inspect_marker}")" -eq 1 ]]

printf '0\n' >"${mirror_marker}"
printf '0\n' >"${mirror_inspect_marker}"
HFL_TEST_MIRROR_INSPECT_MODE=missing-once \
	HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/library/postgres:17 \
		"${digest}" \
		docker.io/example/postgres:17-aaaaaaaaaaaa \
		--if-missing
[[ "$(cat "${mirror_marker}")" -eq 1 ]]
[[ "$(cat "${mirror_inspect_marker}")" -eq 2 ]]

printf '0\n' >"${mirror_marker}"
printf '0\n' >"${mirror_inspect_marker}"
if HFL_TEST_MIRROR_INSPECT_MODE=mismatch \
	HFL_REGISTRY_MIRROR_RETRY_BASE_SECONDS=0 \
	HFL_REGISTRY_MIRROR_RETRY_JITTER_SECONDS=0 \
	"${ROOT}/release/ci/mirror-saas-image.sh" \
		docker.io/library/postgres:17 \
		"${digest}" \
		docker.io/example/postgres:17-aaaaaaaaaaaa \
		--if-missing \
		>/dev/null 2>&1; then
	printf 'ERROR: immutable mirror tag accepted a conflicting digest\n' >&2
	exit 1
fi
[[ "$(cat "${mirror_marker}")" -eq 0 ]]
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
HFL_TEST_MIRROR_MODE=flakyStream \
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

transient_marker="${tmp}/transient-pulls"
rm -f "${tag_marker}" "${transient_marker}"
: >"${pull_marker}"
HFL_TEST_TRANSIENT_REGION=cn HFL_TEST_TRANSIENT_MARKER="${transient_marker}" \
	HFL_REGISTRY_PULL_RETRY_DELAY_SECONDS=0 HFL_REGISTRY_REGION=cn \
	load_images_from_manifest 0 "${package_root}"
[[ -f "${tag_marker}" ]]
[[ "$(wc -l <"${pull_marker}")" -eq 2 ]]
[[ "$(sed -n '1p' "${pull_marker}")" == registry.example.cn/* ]]
[[ "$(sed -n '2p' "${pull_marker}")" == registry.example.cn/* ]]

rm -f "${tag_marker}" "${transient_marker}"
: >"${pull_marker}"
HFL_TEST_TRANSIENT_REGION=cn HFL_TEST_TRANSIENT_FAILURES=2 \
	HFL_TEST_TRANSIENT_MARKER="${transient_marker}" \
	HFL_REGISTRY_PULL_RETRY_DELAY_SECONDS=0 HFL_REGISTRY_REGION=cn \
	load_images_from_manifest 0 "${package_root}"
[[ -f "${tag_marker}" ]]
[[ "$(wc -l <"${pull_marker}")" -eq 3 ]]
[[ "$(sed -n '1p' "${pull_marker}")" == registry.example.cn/* ]]
[[ "$(sed -n '2p' "${pull_marker}")" == registry.example.cn/* ]]
[[ "$(sed -n '3p' "${pull_marker}")" == docker.io/* ]]

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

# Assemble the same thin Candidate consumed by the SaaS deployment. This
# catches drift between upstream metadata, Compose refs, and the installer
# manifest without pulling or rebuilding any third-party image.
candidate_metadata="${tmp}/candidate-metadata"
candidate_archive="${tmp}/candidate.tar.gz"
candidate_extract="${tmp}/candidate-extract"
mkdir -p "${candidate_metadata}" "${candidate_extract}"
python3 - "${candidate_metadata}" "${ROOT}/deploy/online/sourcelens/runtime.json" \
	"${ROOT}/tools/dependencies/versions/runtime-images.env" "${digest}" "${revision}" <<'PY'
import json
import pathlib
import re
import sys

target = pathlib.Path(sys.argv[1])
sourcelens = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
runtime_values = {}
for line in pathlib.Path(sys.argv[3]).read_text(encoding="utf-8").splitlines():
    match = re.fullmatch(r"([A-Z_]+)=(.+)", line.strip())
    if match:
        runtime_values[match.group(1)] = match.group(2)
hfl_digest = sys.argv[4]


def write(name, local_ref, role, digest, global_ref, cn_ref, **extra):
    payload = {
        "component": name,
        "role": role,
        "local_ref": local_ref,
        "digest": digest,
        "platform": "linux/amd64",
        "sources": [
            {"region": "cn", "ref": cn_ref},
            {"region": "global", "ref": global_ref},
        ],
        **extra,
    }
    (target / f"{name}.json").write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )


for component in ("backend", "frontend"):
    local_ref = f"hyperfilelens-{component}:1.0.0-ee"
    write(
        f"hfl-{component}",
        local_ref,
        "hyperfilelens",
        hfl_digest,
        f"docker.io/example/{local_ref}",
        f"registry.example.cn/example/{local_ref}",
    )
for component, image in sourcelens["images"].items():
    write(
        f"sourcelens-{component}",
        image["local_ref"],
        f"sourcelens-{component}",
        image["digest"],
        image["sources"]["global"],
        image["sources"]["cn"],
        sourcelens_version=sourcelens["version"],
        sourcelens_git_ref=sourcelens["git_ref"],
        sourcelens_git_commit=sourcelens["git_commit"],
    )
for component, variable, role in (
    ("postgres", "POSTGRES_IMAGE", "shared"),
    ("redis", "REDIS_IMAGE", "shared"),
    ("sourcelens-nginx", "NGINX_IMAGE", "sourcelens-nginx"),
):
    local_ref, pinned_digest = runtime_values[variable].split("@", 1)
    source = f"docker.io/library/{local_ref}"
    write(component, local_ref, role, pinned_digest, source, source)
for kind in ("agent", "gateway", "language"):
    local_ref = f"hyperfilelens-{kind}-assets:1.0.0"
    write(
        f"{kind}-assets",
        local_ref,
        f"{kind}-assets",
        hfl_digest,
        f"docker.io/example/{local_ref}",
        f"registry.example.cn/example/{local_ref}",
        asset_kind=kind,
    )
PY
"${ROOT}/release/ci/assemble-saas-candidate.sh" \
	"${candidate_metadata}" 1.0.0 "${revision}" "$(printf 'c%.0s' {1..40})" \
	/opt/hfl/extensions/hyperfilelens-ee "${candidate_archive}"
tar -xzf "${candidate_archive}" -C "${candidate_extract}"
assembled_root="${candidate_extract}/hyperfilelens-1.0.0-ee-saas"
grep -Fx 'HFL_POSTGRES_IMAGE=postgres:17' "${assembled_root}/.env.example" >/dev/null
grep -Fx 'HFL_REDIS_IMAGE=redis:alpine' "${assembled_root}/.env.example" >/dev/null
grep -F 'image: oneprolabs/sourcelens-backend:0.49.5' \
	"${assembled_root}/sourcelens/docker-compose.yml" >/dev/null
grep -F 'image: nginx:stable-alpine' \
	"${assembled_root}/sourcelens/docker-compose.yml" >/dev/null
python3 - "${assembled_root}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
registry = manifest["delivery"]["registry_images"]
refs = {entry["local_ref"] for entry in registry}
assert refs == {
    "hyperfilelens-backend:1.0.0-ee",
    "hyperfilelens-frontend:1.0.0-ee",
    "oneprolabs/sourcelens-backend:0.49.5",
    "oneprolabs/sourcelens-frontend:0.49.5",
    "nginx:stable-alpine",
    "postgres:17",
    "redis:alpine",
}
assert not any("lensnode" in ref for ref in refs)
build_info = json.loads(
    (root / "sourcelens/BUILD_INFO.json").read_text(encoding="utf-8")
)
assert build_info["lensnode_image"] == "oneprolabs/sourcelens-lensnode:0.49.5"
assert {
    name: build_info["images"][name]["ref"]
    for name in ("nginx", "postgres", "redis")
} == {
    "nginx": "nginx:stable-alpine",
    "postgres": "postgres:17",
    "redis": "redis:alpine",
}
assert not any((root / "images").iterdir())
PY
validate_package_identity "${assembled_root}"

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
