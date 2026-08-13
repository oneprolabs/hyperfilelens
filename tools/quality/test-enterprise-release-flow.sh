#!/usr/bin/env bash
# Fast contracts for Community/Enterprise CI identity and TEST-host retention.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../lib/version.sh
source "${ROOT}/tools/lib/version.sh"

[[ "$(release_package_basename_for_version v1.2.3 abcdef0 community)" \
	== "hyperfilelens-1.2.3.tar.gz" ]]
[[ "$(release_package_basename_for_version v1.2.3 abcdef0 enterprise)" \
	== "hyperfilelens-1.2.3-ee.tar.gz" ]]

# The packaged installer carries its own deletion guards; both canonical
# release names must remain valid when passed directly to `upgrade --from`.
# shellcheck disable=SC1090
source "${ROOT}/deploy/installer/install.sh"
tmp_guard="$(mktemp -d)"
trap 'rm -rf "${tmp_guard}"' EXIT
touch "${tmp_guard}/hyperfilelens-1.2.3.tar.gz" \
	"${tmp_guard}/hyperfilelens-1.2.3-ee.tar.gz" \
	"${tmp_guard}/hyperfilelens-1.2.3-abcdef0.tar.gz" \
	"${tmp_guard}/customer-release.tar.gz"
safe_assert_upgrade_package_file "${tmp_guard}/hyperfilelens-1.2.3.tar.gz"
safe_assert_upgrade_package_file "${tmp_guard}/hyperfilelens-1.2.3-ee.tar.gz"
safe_assert_upgrade_package_file "${tmp_guard}/hyperfilelens-1.2.3-abcdef0.tar.gz"
safe_assert_upgrade_package_file "${tmp_guard}/customer-release.tar.gz"
if (safe_assert_package_basename '../unsafe.tar.gz') >/dev/null 2>&1; then
	printf 'ERROR: unsafe package basename passed validation\n' >&2
	exit 1
fi
python3 - "${tmp_guard}/unsafe.tar.gz" <<'PY'
import io
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:gz") as archive:
    item = tarfile.TarInfo("../escape")
    item.size = 1
    archive.addfile(item, io.BytesIO(b"x"))
PY
if (validate_upgrade_archive_layout "${tmp_guard}/unsafe.tar.gz") >/dev/null 2>&1; then
	printf 'ERROR: unsafe upgrade archive layout passed validation\n' >&2
	exit 1
fi
rm -rf "${tmp_guard}"
trap - EXIT

tmp_extensions="$(mktemp -d)"
trap 'rm -rf "${tmp_extensions}"' EXIT
env_file="${tmp_extensions}/.env"
example_file="${tmp_extensions}/.env.example"
printf '%s\n' 'APP_VERSION=1.2.2-ee' \
	'HFL_EXTENSIONS=/opt/hfl/extensions/platform' >"${env_file}"
printf '%s\n' 'APP_VERSION=1.2.3' >"${example_file}"
reconcile_hfl_extensions_env "${env_file}" "${example_file}"
if grep -F 'HFL_EXTENSIONS=' "${env_file}" >/dev/null; then
	printf 'ERROR: Community upgrade must remove a previous Enterprise extension path\n' >&2
	exit 1
fi
printf '%s\n' 'APP_VERSION=1.2.3-ee' \
	'HFL_EXTENSIONS=/opt/hfl/extensions/platform' >"${example_file}"
reconcile_hfl_extensions_env "${env_file}" "${example_file}"
grep -Fx 'HFL_EXTENSIONS=/opt/hfl/extensions/platform' "${env_file}" >/dev/null
rm -rf "${tmp_extensions}"
trap - EXIT

workflow="${ROOT}/.github/workflows/artifact_pipeline.yml"
entry_enterprise="${ROOT}/.github/workflows/test.yml"
entry_community="${ROOT}/.github/workflows/release.yml"
promotion="${ROOT}/.github/workflows/production_deploy.yml"
grep -F 'name: HFL - Enterprise Build & Deploy' "${entry_enterprise}" >/dev/null
grep -F 'tags:' "${entry_enterprise}" >/dev/null
grep -F 'edition: enterprise' "${entry_enterprise}" >/dev/null
grep -F 'needs: validate-enterprise-build' "${entry_enterprise}" >/dev/null
grep -F 'Manual Enterprise builds must be started from the main branch' \
	"${entry_enterprise}" >/dev/null
grep -F 'name: HFL - Community Release & Deploy' "${entry_community}" >/dev/null
if grep -F 'tags:' "${entry_community}" >/dev/null; then
	printf 'ERROR: Community publishing must be manual\n' >&2
	exit 1
fi
grep -F 'edition: community' "${entry_community}" >/dev/null
grep -F 'needs: validate-community-release' "${entry_community}" >/dev/null
grep -F 'Community releases must be started from the main branch' \
	"${entry_community}" >/dev/null
grep -F "vars.TEST_AUTO_DEPLOY != 'false'" "${workflow}" >/dev/null
grep -F "vars.PROD_AUTO_DEPLOY != 'false'" "${workflow}" >/dev/null
grep -F 'needs.deploy-test.result == '\''success'\''' "${workflow}" >/dev/null
grep -F "vars.COMMUNITY_AUTO_DEPLOY != 'false'" "${workflow}" >/dev/null
grep -F 'ENTERPRISE_EXTENSION_REPOSITORY' "${workflow}" >/dev/null
grep -F 'secrets.ENTERPRISE_EXTENSION_GIT_TOKEN || secrets.HFL_EXTENSION_GIT_TOKEN' \
	"${workflow}" >/dev/null
grep -F 'secrets.COMMUNITY_SSH_PRIVATE_KEY || secrets.PREPROD_SSH_PRIVATE_KEY' \
	"${workflow}" >/dev/null
grep -F 'Materialize Enterprise extension for quality gates' "${workflow}" >/dev/null
grep -F 'HFL_EXTENSIONS=$extensions' "${workflow}" >/dev/null
grep -F 'Enterprise extension contains no discoverable backend tests' "${workflow}" >/dev/null
grep -F '"${extension_test_dirs[@]}"' "${workflow}" >/dev/null
grep -F -- '--top-level-directory "$backend"' "${workflow}" >/dev/null
grep -F 'HFL_EXTENSIONS= uv run python src/backend/manage.py test' "${workflow}" >/dev/null
if grep -Fx '          uv run python src/backend/manage.py test' "${workflow}" >/dev/null; then
	printf 'ERROR: the full Host backend suite must run with the extension socket empty\n' >&2
	exit 1
fi
grep -F 'HFL_EXTENSIONS= HFL_FRONTEND_TEST_SCOPE=host npm run test:ci' \
	"${workflow}" >/dev/null
grep -F 'HFL_FRONTEND_TEST_SCOPE=extension npm run test:ci' "${workflow}" >/dev/null
if grep -Fx '          npm run test:ci' "${workflow}" >/dev/null; then
	printf 'ERROR: Host and Extension frontend test contracts must run separately\n' >&2
	exit 1
fi
awk '
	/^  assemble-release:$/ { inside = 1; next }
	inside && /^  [a-z0-9-]+:$/ { inside = 0 }
	inside && /timeout-minutes: 120/ { found = 1 }
	END { exit(found ? 0 : 1) }
' "${workflow}"
grep -F 'Upload Enterprise candidate to temporary draft' "${workflow}" >/dev/null
grep -F '.github/scripts/stage-enterprise-release.sh "$incoming"' "${workflow}" >/dev/null
if grep -F 'HFL_RELEASE_MAX_SINGLE_BYTES:' "${workflow}" >/dev/null \
	|| grep -F 'HFL_RELEASE_PART_BYTES:' "${workflow}" >/dev/null; then
	printf 'ERROR: Enterprise staging must retain the canonical single archive\n' >&2
	exit 1
fi
transfer="${ROOT}/.github/scripts/stage-enterprise-release.sh"
grep -F 'TEST_RELEASE_DOWNLOAD_PROXY_URL' "${workflow}" >/dev/null
if grep -F 'gh-release-upload.sh' "${transfer}" >/dev/null; then
	printf 'ERROR: TEST staging must not upload the candidate before verification\n' >&2
	exit 1
fi
grep -F 'release-assets.githubusercontent.com' "${transfer}" >/dev/null
grep -F 'ServerAliveInterval=30' "${transfer}" >/dev/null
grep -F '<.github/scripts/download-enterprise-release.sh' "${transfer}" >/dev/null
grep -F 'for attempt in 1 2 3' "${transfer}" >/dev/null
grep -F 'refreshing URL' "${transfer}" >/dev/null
grep -F 'run_remote verify' "${transfer}" >/dev/null
grep -F 'selected = ["MANIFEST.json", "SHA256SUMS", archives[0]]' "${transfer}" >/dev/null
grep -F -- "-H 'Cache-Control: no-cache'" "${transfer}" >/dev/null
grep -F '?hfl_nonce=${request_nonce}' "${transfer}" >/dev/null
grep -F 'GitHub asset download URL is already expired' "${transfer}" >/dev/null
if grep -F 'scp ' "${transfer}" >/dev/null; then
	printf 'ERROR: Enterprise package staging must not copy release assets over SSH\n' >&2
	exit 1
fi
downloader="${ROOT}/.github/scripts/download-enterprise-release.sh"
grep -F -- '--proxy "${download_proxy}"' "${downloader}" >/dev/null
grep -F -- '--continue-at -' "${downloader}" >/dev/null
grep -F -- '--max-time 3000' "${downloader}" >/dev/null
grep -F 'sha256sum --ignore-missing -c SHA256SUMS' "${downloader}" >/dev/null
grep -F 'sha256sum "${archive}" MANIFEST.json >SHA256SUMS.stored' \
	"${downloader}" >/dev/null
grep -F 'Download release candidate from temporary draft' "${workflow}" >/dev/null
python3 - "${workflow}" <<'PY'
import pathlib
import sys

workflow = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
verify = workflow.index("  verify-release:")
publish = workflow.index("  publish-release:")
stage = workflow.index('.github/scripts/stage-enterprise-release.sh "$incoming"')
deploy = workflow.index("  deploy-test:")
production = workflow.index("  promote-production:")
if not verify < publish < stage < deploy < production:
    raise SystemExit("Enterprise release order must be verify, TEST stage, TEST deploy, PROD promotion")
publish_block = workflow[publish:deploy]
if "needs: [prepare, verify-release]" not in publish_block:
    raise SystemExit("Enterprise TEST staging must depend on successful Runner verification")
deploy_block = workflow[deploy:production]
if "needs: [prepare, publish-release]" not in deploy_block:
    raise SystemExit("Enterprise TEST deployment must depend on successful TEST storage")
production_block = workflow[production:]
if "needs: [prepare, deploy-test]" not in production_block:
    raise SystemExit("Enterprise PROD promotion must depend on successful TEST deployment")
PY
awk '
	/^  publish-release:$/ { inside = 1; next }
	inside && /^  [a-z0-9-]+:$/ { inside = 0 }
	inside && /timeout-minutes: 90/ { found = 1 }
	END { exit(found ? 0 : 1) }
' "${workflow}"
if grep -F 'Download Enterprise release candidate from TEST host' "${workflow}" >/dev/null; then
	printf 'ERROR: Enterprise verification must not copy the package back over SCP\n' >&2
	exit 1
fi
grep -F 'enterprise_commit: ${{ steps.enterprise-ref.outputs.commit }}' "${workflow}" >/dev/null
grep -F 'Check immutable Enterprise store' "${workflow}" >/dev/null
grep -F 'ENTERPRISE_STORED: ${{ steps.enterprise-store.outputs.stored }}' "${workflow}" >/dev/null
grep -F 'stored Enterprise release identity differs' "${workflow}" >/dev/null
if grep -F '"image_version": f"{version}-ee"' "${workflow}" >/dev/null; then
	printf 'ERROR: stored Enterprise package validation derives image identity from version\n' >&2
	exit 1
fi
grep -F 'stored Enterprise runtime image identity is incomplete' "${workflow}" >/dev/null
grep -F 'stored Enterprise {role} runtime image is not in the HFL archive' \
	"${workflow}" >/dev/null
grep -F 'flock -s 9' "${workflow}" >/dev/null
grep -F -- '--expected-commit "$ENTERPRISE_COMMIT"' "${workflow}" >/dev/null
grep -F 'validate_build_contract()' "${workflow}" >/dev/null
grep -F 'Selected tag predates the edition-aware release contract' "${workflow}" >/dev/null
grep -F 'local image_version="${HFL_IMAGE_VERSION:-${HFL_VERSION}}"' \
	"${ROOT}/release/build.sh" >/dev/null
grep -F 'tar xzf hyperfilelens-${version}${edition_suffix}.tar.gz' \
	"${ROOT}/release/build.sh" >/dev/null
grep -F '"hyperfilelens-backend:${image_version}"' "${ROOT}/release/build.sh" >/dev/null
grep -F '"hyperfilelens-frontend:${image_version}"' "${ROOT}/release/build.sh" >/dev/null
grep -F 'sub_key("HFL_GATEWAY_VERSION", image_version)' \
	"${ROOT}/deploy/installer/install.sh" >/dev/null
grep -F 'Enterprise release package has an invalid extension_commit' \
	"${ROOT}/deploy/installer/install.sh" >/dev/null
grep -F '"ls-remote"' "${workflow}" >/dev/null
grep -F 'f"refs/tags/{tag}"' "${workflow}" >/dev/null
grep -F 'gh release delete "$ARTIFACT_ID"' "${workflow}" >/dev/null
if grep -F 'gh release delete "$ARTIFACT_ID"' "${workflow}" \
	| grep -F -- '--cleanup-tag' >/dev/null; then
	printf 'ERROR: temporary Enterprise releases have no Git tag to clean up\n' >&2
	exit 1
fi

# Exercise the target-side downloader without network access. The fake curl
# records its arguments and materializes the two expected assets, proving that
# the canonical archive is downloaded through the configured proxy and then
# covered by SHA256SUMS.
download_test="$(mktemp -d)"
trap 'rm -rf "${download_test}"' EXIT
mkdir -p "${download_test}/bin" "${download_test}/.incoming/candidate"
archive_name=hyperfilelens-1.2.3-ee.tar.gz
printf 'enterprise archive\n' >"${download_test}/${archive_name}"
printf '{"edition":"enterprise"}\n' >"${download_test}/MANIFEST.json"
archive_digest="$(sha256sum "${download_test}/${archive_name}" | awk '{print $1}')"
manifest_digest="$(sha256sum "${download_test}/MANIFEST.json" | awk '{print $1}')"
printf '%s  %s\n%s  %s\n' \
	"${archive_digest}" "${archive_name}" \
	"${manifest_digest}" MANIFEST.json >"${download_test}/SHA256SUMS"
# Preserve the production downloader verbatim except for its fixed TEST-store
# root, which an unprivileged CI contract test cannot create under /root.
sed "s#/root/hfl-release#${download_test}#g" "${downloader}" \
	>"${download_test}/downloader"
chmod +x "${download_test}/downloader"
cat >"${download_test}/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >>"${HFL_FAKE_CURL_LOG}"
printf '\n' >>"${HFL_FAKE_CURL_LOG}"
output=""
url=""
while (($#)); do
	case "$1" in
	--output) output=$2; shift 2 ;;
	https://*) url=$1; shift ;;
	*) shift ;;
	esac
done
[[ -n "${output}" && -n "${url}" ]]
cp "${HFL_FAKE_ASSET_ROOT}/${url##*/}" "${output}"
SH
chmod +x "${download_test}/bin/curl"
plan="${archive_name}"$'\t'"https://release-assets.githubusercontent.com/${archive_name}"$'\n'
plan+="SHA256SUMS"$'\t'"https://release-assets.githubusercontent.com/SHA256SUMS"$'\n'
plan+="MANIFEST.json"$'\t'"https://release-assets.githubusercontent.com/MANIFEST.json"$'\n'
PATH="${download_test}/bin:${PATH}" \
	HFL_FAKE_ASSET_ROOT="${download_test}" \
	HFL_FAKE_CURL_LOG="${download_test}/curl.log" \
	"${download_test}/downloader" \
	"${download_test}/.incoming/candidate" \
	http://192.0.2.10:7890 \
	"$(printf '%s' "${plan}" | base64 -w 0)" \
	download >/dev/null
"${download_test}/downloader" \
	"${download_test}/.incoming/candidate" \
	http://192.0.2.10:7890 '' verify >/dev/null
cmp "${download_test}/${archive_name}" \
	"${download_test}/.incoming/candidate/${archive_name}"
grep -F -- '--proxy http://192.0.2.10:7890' "${download_test}/curl.log" >/dev/null
grep -F -- '--continue-at -' "${download_test}/curl.log" >/dev/null
if find "${download_test}/.incoming/candidate" -maxdepth 1 -name '*.part-*' | grep -q .; then
	printf 'ERROR: Enterprise target-side staging unexpectedly created split assets\n' >&2
	exit 1
fi
rm -rf "${download_test}"
trap - EXIT

checkout_count="$(grep -c 'uses: actions/checkout@' "${workflow}")"
credentialless_checkout_count="$(grep -c 'persist-credentials: false' "${workflow}")"
if [[ "${credentialless_checkout_count}" -ne "${checkout_count}" ]]; then
	printf 'ERROR: every release checkout must disable persisted credentials (%s/%s)\n' \
		"${credentialless_checkout_count}" "${checkout_count}" >&2
	exit 1
fi

# actions/checkout leaves an Authorization extraheader in local Git config. The
# extension auth environment must reset it before adding the private-repo token,
# otherwise GitHub rejects the duplicate Authorization headers with HTTP 400.
HFL_EXTENSION_GIT_TOKEN=01234567890123456789 python3 - "${ROOT}" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from tools.extensions.materialize_extensions import _git_env

env = _git_env(
    "https://github.com/oneprolabs/hyperfilelens-ee.git",
    require_https_auth=True,
)
assert env["GIT_CONFIG_COUNT"] == "2"
assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
assert env["GIT_CONFIG_VALUE_0"] == ""
assert env["GIT_CONFIG_KEY_1"] == env["GIT_CONFIG_KEY_0"]
assert env["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic ")
PY

grep -F 'uses: ./.github/workflows/enterprise_promotion.yml' "${promotion}" >/dev/null
grep -F 'workflow_dispatch:' "${promotion}" >/dev/null
grep -F 'needs: validate-production-promotion' "${promotion}" >/dev/null
grep -F '[[ "$GITHUB_REF" == "refs/heads/main" ]]' "${promotion}" >/dev/null
grep -F "ref: \${{ inputs.automatic && inputs.tag || 'main' }}" \
	"${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'ssh-agent -s' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
promotion_transfer="${ROOT}/.github/scripts/promote-enterprise-release.sh"
[[ -x "${promotion_transfer}" ]]
grep -F 'flock -s 9' "${promotion_transfer}" >/dev/null
promotion_no_stdin_ssh_count="$(grep -c \
	'^[[:space:]]*ssh -n ' \
	"${ROOT}/.github/workflows/enterprise_promotion.yml")"
[[ "${promotion_no_stdin_ssh_count}" -eq 3 ]] || {
	printf 'ERROR: every non-script PROD promotion SSH must disable stdin (found %s/3)\n' \
		"${promotion_no_stdin_ssh_count}" >&2
	exit 1
}
[[ "$(grep -c 'ServerAliveInterval=30' "${ROOT}/.github/workflows/enterprise_promotion.yml")" -eq 4 ]]
grep -F 'ServerAliveCountMax=20' "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'ServerAliveInterval=30' "${promotion_transfer}" >/dev/null
grep -F 'ServerAliveCountMax=20' "${promotion_transfer}" >/dev/null
grep -F 'rsync --archive --checksum --partial --partial-dir=.rsync-partial' \
	"${promotion_transfer}" >/dev/null
grep -F -- '--info=progress2' "${promotion_transfer}" >/dev/null
grep -F 'for attempt in 1 2 3' "${promotion_transfer}" >/dev/null
grep -F 'retaining partial data' "${promotion_transfer}" >/dev/null
grep -F 'already retained on PROD' "${promotion_transfer}" >/dev/null
if grep -F 'rm -rf -- "${incoming}"' "${promotion_transfer}" >/dev/null \
	|| grep -F 'scp ' "${promotion_transfer}" >/dev/null; then
	printf 'ERROR: PROD promotion must retain resumable partial data and must not copy the archive with scp\n' >&2
	exit 1
fi
grep -F "cd '\${store_root}/\${tag}' && sha256sum -c SHA256SUMS" \
	"${promotion_transfer}" >/dev/null
grep -F "printf -v remote_command '%q '" \
	"${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null
grep -F 'expected_edition=enterprise' "${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'flock -s 8' "${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'validate_release_archive_layout "${package}"' \
	"${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'release archive link escapes its package root' \
	"${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F "printf -v remote_command '%q '" \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'release has multiple legacy package candidates' \
	"${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'ERROR: no checksum for %s' \
	"${ROOT}/.github/scripts/remote-deploy.sh" >/dev/null
grep -F 'community:github|test:host-store|prod:host-store' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'PACKAGE_SOURCE: ${{ inputs.package_source }}' \
	"${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'hfl_artifact += "-ee"' "${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
grep -F 'hyperfilelens-agent@{artifact}' "${ROOT}/.github/workflows/deploy_target.yml" >/dev/null
if grep -E '^[[:space:]]+"\$RUNNER_TEMP/prod-key"[[:space:]]' \
	"${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null; then
	printf 'ERROR: the PROD private key must not be copied onto the TEST host\n' >&2
	exit 1
fi
if grep -R -F 'TESTED' "${promotion}" "${ROOT}/.github/workflows/enterprise_promotion.yml" >/dev/null; then
	printf 'ERROR: manual Enterprise production promotion must not require TESTED\n' >&2
	exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

selector="${tmp}/select-community-release-assets.py"
sed -n \
	'/^# BEGIN COMMUNITY RELEASE ASSET SELECTOR$/,/^# END COMMUNITY RELEASE ASSET SELECTOR$/p' \
	"${ROOT}/.github/scripts/remote-deploy.sh" \
	| sed '1d;$d' \
	| sed '1d;$d' >"${selector}"

write_release_json() {
	python3 - "$1" "${@:2}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
names = sys.argv[2:]
path.write_text(
    json.dumps(
        {
            "draft": False,
            "tag_name": "v1.2.3",
            "assets": [
                {"name": name, "browser_download_url": f"https://example.invalid/{name}"}
                for name in ["SHA256SUMS", *names]
            ],
        }
    ),
    encoding="utf-8",
)
PY
}

release_json="${tmp}/release.json"
assets_tsv="${tmp}/assets.tsv"
write_release_json "$release_json" \
	"hyperfilelens-1.2.3.tar.gz" \
	"hyperfilelens-1.2.3-abcdef0.tar.gz"
python3 "$selector" "$release_json" "$assets_tsv" v1.2.3
grep -F $'hyperfilelens-1.2.3.tar.gz\thttps://example.invalid/hyperfilelens-1.2.3.tar.gz' \
	"$assets_tsv" >/dev/null
if grep -F 'hyperfilelens-1.2.3-abcdef0.tar.gz' "$assets_tsv" >/dev/null; then
	printf 'ERROR: current Community package must take precedence over a legacy package\n' >&2
	exit 1
fi

write_release_json "$release_json" \
	"hyperfilelens-1.2.3-abcdef0.tar.gz.part-000" \
	"hyperfilelens-1.2.3-abcdef0.tar.gz.part-001"
python3 "$selector" "$release_json" "$assets_tsv" v1.2.3
grep -F 'hyperfilelens-1.2.3-abcdef0.tar.gz.part-000' "$assets_tsv" >/dev/null
grep -F 'hyperfilelens-1.2.3-abcdef0.tar.gz.part-001' "$assets_tsv" >/dev/null

write_release_json "$release_json" \
	"hyperfilelens-1.2.3-abcdef0.tar.gz" \
	"hyperfilelens-1.2.3-1234567.tar.gz"
if python3 "$selector" "$release_json" "$assets_tsv" v1.2.3 2>"${tmp}/selector-error"; then
	printf 'ERROR: ambiguous legacy Community packages must be rejected\n' >&2
	exit 1
fi
grep -F 'multiple legacy package candidates' "${tmp}/selector-error" >/dev/null

store="${tmp}/hfl-release"
mkdir -p "${store}/.incoming"

make_candidate() {
	local number=$1 marker=${2:-} extension_commit=${3:-89abcdef0123456789abcdef0123456789abcdef}
	local tag version incoming root archive
	version="1.0.${number}"
	tag="v${version}"
	incoming="${store}/.incoming/${number}"
	root="${tmp}/package-${number}/hyperfilelens-${version}-ee"
	archive="${incoming}/hyperfilelens-${version}-ee.tar.gz"
	mkdir -p "${incoming}" "${root}"
	printf '%s\n' "${version}" >"${root}/VERSION"
	printf '#!/usr/bin/env bash\n' >"${root}/install.sh"
	chmod +x "${root}/install.sh"
	[[ -z "${marker}" ]] || printf '%s\n' "${marker}" >"${root}/BUILD-MARKER"
	cat >"${root}/MANIFEST.json" <<JSON
{"schema_version":2,"product":"hyperfilelens","edition":"enterprise","image_version":"build-${number}","runtime_images":{"backend":"hyperfilelens-backend:build-${number}","frontend":"hyperfilelens-frontend:build-${number}"},"channel":"release","artifact_id":"${tag}","version":"${version}","git_commit":"0123456789abcdef0123456789abcdef01234567","extension_commit":"${extension_commit}","images":[{"file":"images/hfl.tar.gz","refs":["hyperfilelens-backend:build-${number}","hyperfilelens-frontend:build-${number}"],"role":"hyperfilelens"}]}
JSON
	cp "${root}/MANIFEST.json" "${incoming}/MANIFEST.json"
	tar -C "$(dirname "${root}")" -czf "${archive}" "$(basename "${root}")"
	(cd "${incoming}" && sha256sum "$(basename "${archive}")" MANIFEST.json >SHA256SUMS)
	"${ROOT}/.github/scripts/store-enterprise-release.sh" \
		"${tag}" "${incoming}" "${store}" 10 >/dev/null
}

for number in $(seq 0 10); do
	make_candidate "${number}"
done
[[ "$(find "${store}" -mindepth 1 -maxdepth 1 -type d -name 'v*' | wc -l)" -eq 10 ]]
[[ ! -e "${store}/v1.0.0" ]]
[[ -s "${store}/v1.0.10/hyperfilelens-1.0.10-ee.tar.gz" ]]
[[ "$(find "${store}/v1.0.10" -maxdepth 1 -type f | wc -l)" -eq 3 ]]
(cd "${store}/v1.0.10" && sha256sum -c SHA256SUMS >/dev/null)

# Revalidating an old version must not make it displace a newer SemVer.
if make_candidate 0 2>"${tmp}/retention-error"; then
	printf 'ERROR: an Enterprise version outside the retention window must not be deployable\n' >&2
	exit 1
fi
grep -F 'is outside the retained SemVer window' "${tmp}/retention-error" >/dev/null
[[ ! -e "${store}/v1.0.0" ]]
[[ -e "${store}/v1.0.1" && -e "${store}/v1.0.10" ]]

# The store accepts an exact retry without replacing the retained package.
stored_digest="$(sha256sum "${store}/v1.0.10/hyperfilelens-1.0.10-ee.tar.gz" | awk '{print $1}')"
exact_retry="${store}/.incoming/exact-retry"
cp -a "${store}/v1.0.10" "${exact_retry}"
"${ROOT}/.github/scripts/store-enterprise-release.sh" \
	v1.0.10 "${exact_retry}" "${store}" 10 >/dev/null
[[ "$(sha256sum "${store}/v1.0.10/hyperfilelens-1.0.10-ee.tar.gz" | awk '{print $1}')" \
	== "${stored_digest}" ]]

# Different output for the same source identity is not an immutable retry.
if make_candidate 10 changed-output 2>"${tmp}/content-error"; then
	printf 'ERROR: an immutable Enterprise version accepted different package bytes\n' >&2
	exit 1
fi
grep -F 'immutable Enterprise package content differs' "${tmp}/content-error" >/dev/null

# Moving either source tag must not make an immutable version appear to rebuild.
if make_candidate 10 changed-source fedcba9876543210fedcba9876543210fedcba98 \
	2>"${tmp}/identity-error"; then
	printf 'ERROR: an immutable Enterprise version accepted different source identity\n' >&2
	exit 1
fi
grep -F 'immutable Enterprise release identity differs' "${tmp}/identity-error" >/dev/null

printf 'Enterprise release flow contracts passed.\n'
