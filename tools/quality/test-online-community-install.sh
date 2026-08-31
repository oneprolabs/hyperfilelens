#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
workflow="${ROOT}/.github/workflows/enterprise_saas_upgrade.yml"
online="${ROOT}/deploy/online"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
export PYTHONDONTWRITEBYTECODE=1

bash -n "${online}/install.sh"
PYTHONPYCACHEPREFIX="${tmp}/pycache" python3 -m py_compile "${online}/prepare.py"

help_output="$("${online}/install.sh" --help)"
grep -Fq -- '--mirror cn|global' <<<"${help_output}"
grep -Fq -- '--tag vX.Y.Z' <<<"${help_output}"
grep -Fq -- '--yes' <<<"${help_output}"
if grep -Eq -- '--region|--download-source|install\.sh vX\.Y\.Z' <<<"${help_output}"; then
	printf 'ERROR: online installer exposes a retired public argument\n' >&2
	exit 1
fi
grep -Fq 'https://codeload.github.com/oneprolabs/hyperfilelens/tar.gz/${RELEASE_COMMIT}' \
	"${online}/install.sh"
grep -Fq 'https://gitee.com/oneprolabs/hyperfilelens/repository/archive/${RELEASE_COMMIT}.tar.gz' \
	"${online}/install.sh"
grep -Fq 'api.github.com/repos/oneprolabs/hyperfilelens/tags?per_page=100&page=1' \
	"${online}/install.sh"
grep -Fq 'gitee.com/api/v5/repos/oneprolabs/hyperfilelens/tags?per_page=100&page=1' \
	"${online}/install.sh"
grep -Fq 'recent fallback tags:' "${online}/install.sh"
grep -Fq 'prepared Community image revision does not match the published release' \
	"${online}/install.sh"
grep -Fq 'run this command through sudo' "${online}/install.sh"
grep -Fq -- '--yes                   Non-interactive compatibility flag' \
	"${ROOT}/deploy/installer/install.sh"

grep -Fq 'name: HFL - Publish Images & Upgrade SaaS' "${workflow}"
grep -Fq 'Publish · ${{ github.event_name == '\''push'\'' && github.ref_name || inputs.tag }} · Images · Enterprise SaaS Upgrade' "${workflow}"
grep -Fq 'name: Community · Backend' "${workflow}"
grep -Fq 'name: Enterprise · Backend' "${workflow}"
grep -Fq 'tag_suffix: -ee' "${workflow}"
grep -Fq 'hyperfilelens-agent-assets:${{ needs.prepare.outputs.version }}' "${workflow}"
grep -Fq 'hyperfilelens-gateway-assets:${{ needs.prepare.outputs.version }}' "${workflow}"
grep -Fq 'hyperfilelens-language-assets:${{ needs.prepare.outputs.version }}' "${workflow}"

installer="${ROOT}/deploy/installer/install.sh"
[[ "$(grep -Fc 'if [[ "${HFL_ONLINE_CHILD:-0}" != "1" ]]; then' "${installer}")" -eq 2 ]]
for summary_contract in \
	'print_section "Platform Data Gateway"' \
	'print_section "Published resources"' \
	'print_value "Agent version"' \
	'print_value "Agent service"' \
	'print_value "Console state"' \
	'print_value "Data" "${LOCAL_PLATFORM_AGENT_DATA_DIR}/data"' \
	'print_value "Logs" "${LOCAL_PLATFORM_AGENT_DATA_DIR}/logs"' \
	'print_value "Install log"'; do
	grep -Fq "${summary_contract}" "${installer}"
done
if grep -Eq 'publish-community-channel|community-channel|git push origin HEAD:main' "${workflow}"; then
	printf 'ERROR: SaaS workflow must not manage a separate Community channel branch\n' >&2
	exit 1
fi
grep -Fq 'SOURCELENS_DISTRIBUTION_TAG_OVERRIDE="${version}"' \
	"${ROOT}/release/ci/assemble-saas-candidate.sh"
grep -Fq 'SOURCELENS_DISTRIBUTION_TAG_OVERRIDE: ${{ needs.prepare.outputs.version }}' \
	"${workflow}"
if grep -E 'hyperfilelens-(agent|gateway|language)-assets:.*image_version' "${workflow}"; then
	printf 'ERROR: OSS asset images must not use the Enterprise image suffix\n' >&2
	exit 1
fi

for file in \
	sourcelens/env.example \
	sourcelens/nginx/default.conf \
	sourcelens/postgresql/etc/postgresql.conf \
	sourcelens/postgresql/initdb.d/000-create-databases.sql \
	sourcelens/postgresql/initdb.d/001-grant-schema-privileges.sh \
	sourcelens/postgresql/initdb.d/002-setup-log-permissions.sh \
	sourcelens/runtime.json; do
	[[ -s "${online}/${file}" ]] || {
		printf 'ERROR: missing online runtime template: %s\n' "${file}" >&2
		exit 1
	}
	done

grep -Fq 'NGINX_HTTPS_PORT=11445' "${online}/sourcelens/env.example"
grep -Fq 'LENSNODE_PLANNING_REASONING_EFFORT=medium' \
	"${online}/sourcelens/env.example"
grep -Fq 'LENSNODE_EXECUTION_BACKEND=trusted_container' \
	"${online}/sourcelens/env.example"
grep -Fq 'LENSNODE_MAX_CONCURRENT_RUNS=1' \
	"${online}/sourcelens/env.example"
if grep -q '^LENSNODE_HEAVY_WORK_CONCURRENCY=' \
	"${online}/sourcelens/env.example"; then
	printf 'ERROR: online SourceLens template configures a retired setting\n' >&2
	exit 1
fi
if grep -Eq '104(42|43|44|45|46)' "${online}/sourcelens/env.example"; then
	printf 'ERROR: online SourceLens template references a legacy HFL public port\n' >&2
	exit 1
fi

(
	# shellcheck source=../../tools/sourcelens/common.sh
	source "${ROOT}/tools/sourcelens/common.sh"
	sourcelens_load_config
	SOURCELENS_GIT_REF=v0.47.9
	SOURCELENS_HFL_VERSION=1.2.3
	sourcelens_resolve_version
	[[ "${SOURCELENS_DISTRIBUTION_TAG}" == 1.2.3-sl0.47.9 ]]
)
(
	# shellcheck source=../../tools/sourcelens/common.sh
	source "${ROOT}/tools/sourcelens/common.sh"
	sourcelens_load_config
	SOURCELENS_GIT_REF=v0.47.9
	SOURCELENS_HFL_VERSION=1.2.3
	SOURCELENS_DISTRIBUTION_TAG_OVERRIDE=1.2.3
	sourcelens_resolve_version
	[[ "${SOURCELENS_DISTRIBUTION_TAG}" == 1.2.3 ]]
)

python3 - "${ROOT}" <<'PY'
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import tempfile
import sys

root = pathlib.Path(sys.argv[1])
module_path = root / "deploy/online/prepare.py"
spec = importlib.util.spec_from_file_location("hfl_online_prepare", module_path)
if spec is None or spec.loader is None:
    raise SystemExit("could not load online prepare module")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

runtime = json.loads(
    (root / "deploy/online/sourcelens/runtime.json").read_text(encoding="utf-8")
)
defaults = (root / "tools/sourcelens/defaults.env").read_text(encoding="utf-8")
match = re.search(r"SOURCELENS_GIT_REF=.*?(v\d+\.\d+\.\d+)", defaults)
if match is None or runtime["git_ref"] != match.group(1):
    raise SystemExit("online SourceLens runtime does not match the HFL default ref")

with tempfile.TemporaryDirectory() as temporary:
    output = pathlib.Path(temporary) / "docker-compose.yml"
    module.render_sourcelens_compose(
        root / "deploy/installer/sourcelens/docker-compose.template.yml",
        output,
        "1.2.3",
    )
    compose = output.read_text(encoding="utf-8")
    for component in ("backend", "frontend"):
        expected = f"hyperfilelens-sourcelens-{component}:1.2.3"
        if expected not in compose:
            raise SystemExit(f"rendered SourceLens Compose is missing {expected}")
    if "__SOURCELENS_" in compose or "HFL_EMBED_" in compose:
        raise SystemExit("rendered SourceLens Compose retained a template marker")

with tempfile.TemporaryDirectory() as temporary:
    asset = pathlib.Path(temporary)
    (asset / ".asset-kind").write_text("language\n", encoding="utf-8")
    catalogs = asset / "payload/language-packs"
    catalogs.mkdir(parents=True)
    (catalogs / "test.tar.gz").write_bytes(b"test")
    module.validate_asset_tree(asset, "language")
PY

fake_bin="${tmp}/bin"
candidate="${tmp}/hyperfilelens-1.2.3-online"
mkdir -p "${fake_bin}"
tag_fixture="${tmp}/tags.json"
tag_page_one_fixture="${tmp}/tags-page-1.json"
tag_page_two_fixture="${tmp}/tags-page-2.json"
python3 - "${tag_fixture}" <<'PY'
import json
import pathlib
import sys

tags = [
    {
        "name": f"v1.2.{version}",
        "commit": {"sha": f"{version:x}" * 40},
    }
    for version in range(1, 13)
]
pathlib.Path(sys.argv[1]).write_text(json.dumps(tags), encoding="utf-8")
PY
python3 - "${tag_page_one_fixture}" "${tag_page_two_fixture}" <<'PY'
import json
import pathlib
import sys

first = [
    {"name": f"v1.0.{version}", "commit": {"sha": f"{version:040x}"}}
    for version in range(1, 101)
]
second = [{"name": "v2.0.0", "commit": {"sha": "f" * 40}}]
pathlib.Path(sys.argv[1]).write_text(json.dumps(first), encoding="utf-8")
pathlib.Path(sys.argv[2]).write_text(json.dumps(second), encoding="utf-8")
PY
cat >"${fake_bin}/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
output=""
url=""
original_args=("$@")
[[ -z "${HFL_TEST_CURL_MARKER:-}" ]] || touch "${HFL_TEST_CURL_MARKER}"
if [[ -n "${HFL_TEST_CURL_LOG:-}" ]]; then
	printf '%q ' "${original_args[@]}" >>"${HFL_TEST_CURL_LOG}"
	printf '\n' >>"${HFL_TEST_CURL_LOG}"
fi
case "${1:-} ${2:-}" in
"--retry-all-errors --version")
	if [[ "${HFL_TEST_CURL_SUPPORT_RETRY_ALL_ERRORS:-1}" == "1" ]]; then
		printf 'curl 8.5.0 test-build\n'
		exit 0
	fi
	printf 'curl: option --retry-all-errors: is unknown\n' >&2
	exit 2
	;;
"--retry-connrefused --version")
	if [[ "${HFL_TEST_CURL_SUPPORT_RETRY_CONNREFUSED:-1}" == "1" ]]; then
		printf 'curl 7.68.0 test-build\n'
		exit 0
	fi
	printf 'curl: option --retry-connrefused: is unknown\n' >&2
	exit 2
	;;
"--version ")
	if [[ "${HFL_TEST_CURL_SUPPORT_RETRY_ALL_ERRORS:-1}" == "1" ]]; then
		printf 'curl 8.5.0 test-build\n'
	else
		printf 'curl 7.68.0 test-build\n'
	fi
	exit 0
	;;
esac
if [[ "${HFL_TEST_CURL_SUPPORT_RETRY_ALL_ERRORS:-1}" != "1" ]]; then
	for argument in "${original_args[@]}"; do
		if [[ "${argument}" == "--retry-all-errors" ]]; then
			printf 'curl: option --retry-all-errors: is unknown\n' >&2
			exit 2
		fi
	done
fi
while (($#)); do
	case "$1" in
	-o)
		output=${2:-}
		shift 2
		;;
	http://* | https://*)
		url=$1
		shift
		;;
	*) shift ;;
	esac
done
if [[ "${HFL_TEST_CURL_PARTIAL_FAIL:-0}" == "1" && -n "${output}" ]]; then
	printf 'partial response\n' >"${output}"
	exit 28
fi
if [[ -n "${HFL_TEST_CURL_PAYLOAD:-}" && -n "${output}" ]]; then
	printf '%s\n' "${HFL_TEST_CURL_PAYLOAD}" >"${output}"
	exit 0
fi
if [[ "${url}" == *'/tags?'* && -n "${output}" ]]; then
	page=1
	if [[ "${url}" =~ [\?\&]page=([0-9]+) ]]; then
		page=${BASH_REMATCH[1]}
	fi
	page_fixture_name="HFL_TEST_TAG_FIXTURE_PAGE_${page}"
	page_fixture=${!page_fixture_name:-}
	if [[ -n "${page_fixture}" ]]; then
		cp "${page_fixture}" "${output}"
	elif ((page == 1)); then
		cp "${HFL_TEST_TAG_FIXTURE}" "${output}"
	else
		printf '[]\n' >"${output}"
	fi
	exit 0
fi
exit 22
SH
cat >"${fake_bin}/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
digest="sha256:$(printf 'b%.0s' {1..64})"
revision="$(printf 'c%.0s' {1..40})"
case "${1:-} ${2:-}" in
"info " | "compose version")
	exit 0
	;;
"pull --platform")
	printf '\rDocker native pull progress: %s\n' "${4:-unknown}"
	if [[ "${HFL_TEST_DOCKER_PULL_FAIL:-0}" == "1" ]]; then
		printf 'registry rejected test image: access denied\n' >&2
		exit 23
	fi
	exit 0
	;;
"image inspect")
	ref=${3:-}
	format=${5:-}
	if [[ "${format}" == *RepoDigests* ]]; then
		printf '["%s@%s"]\n' "${ref%:*}" "${digest}"
	else
		printf '%s\n' "${revision}"
	fi
	;;
"buildx imagetools")
	printf '{"digest":"%s"}\n' "${digest}"
	;;
"image rm")
	printf 'Untagged: %s\n' "${3:-unknown}"
	exit 0
	;;
"rm -f")
	printf '%s\n' "${3:-temporary-container}"
	exit 0
	;;
"tag "*)
	exit 0
	;;
"create "*)
	case "${2:-}" in
	*agent-assets*) printf 'cid-agent\n' ;;
	*gateway-assets*) printf 'cid-gateway\n' ;;
	*language-assets*) printf 'cid-language\n' ;;
	*) exit 2 ;;
	esac
	;;
"cp "*)
	source_path=${2:-}
	target=${3:-}
	case "${source_path}" in
	cid-agent:*)
		mkdir -p \
			"${target}/payload/media/agent-releases/${HFL_TEST_VERSION}" \
			"${target}/payload/media/enroll-bootstrap/${HFL_TEST_VERSION}"
		printf 'agent\n' >"${target}/.asset-kind"
		printf 'agent\n' >"${target}/payload/media/agent-releases/${HFL_TEST_VERSION}/agent.tar.gz"
		printf 'installer\n' >"${target}/payload/media/enroll-bootstrap/${HFL_TEST_VERSION}/hfl-installer-linux-amd64.tar.gz"
		;;
	cid-gateway:*)
		root="${target}/payload/media/gateway-bootstrap"
		mkdir -p "${root}"
		printf 'gateway\n' >"${target}/.asset-kind"
		for file in \
			gateway-bootstrap-linux.sh \
			gateway-install-lensnode-sidecar.sh \
			gateway-lifecycle.sh \
			gateway-install-docker-ubuntu-amd64.sh \
			hfl-sentry-sitecustomize.py \
			docker-debs-ubuntu2004-amd64.tar.gz \
			docker-debs-ubuntu2204-amd64.tar.gz \
			docker-debs-ubuntu2404-amd64.tar.gz \
			lensnode-image-linux-amd64.tar.gz; do
			printf 'gateway\n' >"${root}/${file}"
		done
		;;
	cid-language:*)
		mkdir -p "${target}/payload/language-packs"
		printf 'language\n' >"${target}/.asset-kind"
		printf 'language\n' >"${target}/payload/language-packs/hyperfilelens-lang-zh-hans-${HFL_TEST_VERSION}.tar.gz"
		;;
	*) exit 2 ;;
	esac
	;;
*)
	printf 'unexpected fake docker invocation: %s\n' "$*" >&2
	exit 2
	;;
esac
SH
chmod 755 "${fake_bin}/curl" "${fake_bin}/docker"

online_functions="${tmp}/online-install-functions.sh"
python3 - "${online}/install.sh" "${online_functions}" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
marker = "\nwhile (($#)); do\n"
if source.count(marker) != 1:
    raise SystemExit("online installer entrypoint marker is ambiguous")
pathlib.Path(sys.argv[2]).write_text(source.split(marker, 1)[0], encoding="utf-8")
PY
atomic_target="${tmp}/atomic-download.json"
printf 'preserved response\n' >"${atomic_target}"
if (
	export PATH="${fake_bin}:${PATH}"
	export HFL_TEST_CURL_PARTIAL_FAIL=1
	# shellcheck disable=SC1090
	source "${online_functions}"
	configure_curl_retry_options
	download_file "https://example.test/partial" "${atomic_target}"
); then
	printf 'ERROR: interrupted online download unexpectedly succeeded\n' >&2
	exit 1
fi
grep -Fxq 'preserved response' "${atomic_target}"
[[ ! -e "${atomic_target}.part" ]] || {
	printf 'ERROR: interrupted online download replaced the target or retained a partial artifact\n' >&2
	exit 1
}
(
	export PATH="${fake_bin}:${PATH}"
	export HFL_TEST_CURL_PAYLOAD='complete response'
	# shellcheck disable=SC1090
	source "${online_functions}"
	configure_curl_retry_options
	download_file "https://example.test/complete" "${atomic_target}"
)
grep -Fxq 'complete response' "${atomic_target}"
[[ ! -e "${atomic_target}.part" ]]

test_install_root="${tmp}/community-install"
test_installer="${tmp}/online-install.sh"
python3 - "${online}/install.sh" "${test_installer}" "${test_install_root}" "${tmp}" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
replacements = {
    'INSTALL_ROOT="/opt/hyperfilelens"': f'INSTALL_ROOT="{sys.argv[3]}"',
    '[[ "${EUID}" -eq 0 ]] || fail "run this command through sudo"': ":",
    "\ninstall_host_tools\ninspect_existing_installation\n": (
        "\n:\ninspect_existing_installation\n"
    ),
    'SESSION_DIR="$(mktemp -d /var/tmp/hyperfilelens-online.XXXXXX)"': (
        f'SESSION_DIR="$(mktemp -d "{sys.argv[4]}/online-session.XXXXXX")"'
    ),
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise SystemExit(f"online installer test fixture replacement is ambiguous: {old}")
    source = source.replace(old, new, 1)
pathlib.Path(sys.argv[2]).write_text(source, encoding="utf-8")
PY
chmod 755 "${test_installer}"

enterprise_root="${tmp}/enterprise-install"
mkdir -p "${enterprise_root}"
printf 'HFL_EDITION=enterprise\n' >"${enterprise_root}/.env"
printf '{"edition":"enterprise"}\n' >"${enterprise_root}/MANIFEST.json"
enterprise_installer="${tmp}/enterprise-rejection-install.sh"
python3 - "${test_installer}" "${enterprise_installer}" "${enterprise_root}" <<'PY'
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
source, count = re.subn(
    r'^INSTALL_ROOT="[^"]+"$',
    f'INSTALL_ROOT="{sys.argv[3]}"',
    source,
    count=1,
    flags=re.MULTILINE,
)
if count != 1:
    raise SystemExit("could not replace the online installer test root")
pathlib.Path(sys.argv[2]).write_text(source, encoding="utf-8")
PY
chmod 755 "${enterprise_installer}"
enterprise_log="${tmp}/enterprise-rejection.log"
curl_marker="${tmp}/enterprise-curl-called"
if PATH="${fake_bin}:${PATH}" HFL_TEST_CURL_MARKER="${curl_marker}" \
	"${enterprise_installer}" --mirror global --yes >"${enterprise_log}" 2>&1; then
	printf 'ERROR: Community installer accepted an Enterprise installation\n' >&2
	exit 1
fi
grep -Fq 'this public installer upgrades Community only' "${enterprise_log}" || {
	cat "${enterprise_log}" >&2
	exit 1
}
[[ ! -e "${curl_marker}" ]] || {
	printf 'ERROR: Community installer contacted the release source before rejecting Enterprise\n' >&2
	exit 1
}

latest_log="${tmp}/latest-tag.log"
modern_curl_log="${tmp}/curl-modern.log"
if PATH="${fake_bin}:${PATH}" HFL_TEST_TAG_FIXTURE="${tag_fixture}" \
	HFL_TEST_CURL_LOG="${modern_curl_log}" \
	"${test_installer}" --mirror global --yes >"${latest_log}" 2>&1; then
	printf 'ERROR: fake online install unexpectedly succeeded\n' >&2
	exit 1
fi
grep -Fq 'Version        v1.2.12' "${latest_log}" || {
	cat "${latest_log}" >&2
	exit 1
}
latest_session_log="$(find "${test_install_root}/logs" -maxdepth 1 -type f \
	-name 'install-*.log' -print -quit)"
[[ -n "${latest_session_log}" ]]
grep -Fq 'HyperFileLens Community Online Installer' "${latest_session_log}"
grep -Fq 'Resolving Community tags from GitHub' "${latest_session_log}"
grep -Fq "Log file       ${latest_session_log}" "${latest_log}"
grep -Fq 'recent fallback tags: v1.2.11, v1.2.10, v1.2.9, v1.2.8, v1.2.7, v1.2.6, v1.2.5, v1.2.4, v1.2.3, v1.2.2' \
	"${latest_log}"
grep -Fq 'recommended retry: --mirror global --tag v1.2.11' "${latest_log}"
grep -v -- '--version' "${modern_curl_log}" \
	| grep -F -- '--retry-all-errors' >/dev/null
if grep -Fq 'using Ubuntu-compatible retry options' "${latest_log}"; then
	printf 'ERROR: modern curl unexpectedly selected compatibility retry mode\n' >&2
	exit 1
fi

compatible_log="${tmp}/curl-7.68-install.log"
compatible_curl_log="${tmp}/curl-7.68-args.log"
if PATH="${fake_bin}:${PATH}" HFL_TEST_TAG_FIXTURE="${tag_fixture}" \
	HFL_TEST_CURL_SUPPORT_RETRY_ALL_ERRORS=0 \
	HFL_TEST_CURL_LOG="${compatible_curl_log}" \
	"${test_installer}" --mirror global --yes >"${compatible_log}" 2>&1; then
	printf 'ERROR: fake curl 7.68 online install unexpectedly succeeded\n' >&2
	exit 1
fi
grep -Fq '[INFO] curl 7.68.0 detected; using Ubuntu-compatible retry options.' \
	"${compatible_log}"
grep -Fq '[....] Downloading v1.2.12 installation contract from GitHub' \
	"${compatible_log}"
grep -v -- '--version' "${compatible_curl_log}" \
	| grep -F -- '--retry-connrefused' >/dev/null
if grep -v -- '--version' "${compatible_curl_log}" \
	| grep -F -- '--retry-all-errors' >/dev/null; then
	printf 'ERROR: curl 7.68 downloads received --retry-all-errors\n' >&2
	exit 1
fi
if grep -Fq 'option --retry-all-errors: is unknown' "${compatible_log}"; then
	printf 'ERROR: curl capability probing leaked an unsupported-option error\n' >&2
	exit 1
fi

missing_log="${tmp}/missing-tag.log"
if PATH="${fake_bin}:${PATH}" HFL_TEST_TAG_FIXTURE="${tag_fixture}" \
	"${test_installer}" --mirror global --tag v9.9.9 --yes \
	>"${missing_log}" 2>&1; then
	printf 'ERROR: missing online tag unexpectedly succeeded\n' >&2
	exit 1
fi
grep -Fq 'Community tag v9.9.9 does not exist on GitHub' "${missing_log}"
grep -Fq 'recent fallback tags: v1.2.12, v1.2.11, v1.2.10, v1.2.9, v1.2.8, v1.2.7, v1.2.6, v1.2.5, v1.2.4, v1.2.3' \
	"${missing_log}"
grep -Fq 'recommended retry: --mirror global --tag v1.2.12' "${missing_log}"

pagination_log="${tmp}/pagination.log"
if PATH="${fake_bin}:${PATH}" \
	HFL_TEST_TAG_FIXTURE="${tag_page_one_fixture}" \
	HFL_TEST_TAG_FIXTURE_PAGE_2="${tag_page_two_fixture}" \
	"${test_installer}" --mirror global --yes >"${pagination_log}" 2>&1; then
	printf 'ERROR: fake paginated online install unexpectedly succeeded\n' >&2
	exit 1
fi
grep -Fq 'Version        v2.0.0' "${pagination_log}" || {
	cat "${pagination_log}" >&2
	exit 1
}

repeated_page_log="${tmp}/repeated-page.log"
if PATH="${fake_bin}:${PATH}" \
	HFL_TEST_TAG_FIXTURE="${tag_page_one_fixture}" \
	HFL_TEST_TAG_FIXTURE_PAGE_2="${tag_page_one_fixture}" \
	"${test_installer}" --mirror global --yes >"${repeated_page_log}" 2>&1; then
	printf 'ERROR: repeated tag page unexpectedly succeeded\n' >&2
	exit 1
fi
grep -Fq 'repeated page 2' "${repeated_page_log}"

prepare_log="${tmp}/prepare.log"
PATH="${fake_bin}:${PATH}" HFL_TEST_VERSION=1.2.3 \
	HFL_ONLINE_NATIVE_PROGRESS=1 \
	python3 "${online}/prepare.py" \
		--source-root "${ROOT}" \
		--version v1.2.3 \
		--region global \
		--output "${candidate}" >"${prepare_log}" 2>&1
grep -F 'Docker native pull progress:' "${prepare_log}" >/dev/null
if grep -F 'Untagged:' "${prepare_log}" >/dev/null; then
	printf 'ERROR: temporary asset image cleanup leaked into online output\n' >&2
	exit 1
fi
if grep -F 'cid-' "${prepare_log}" >/dev/null; then
	printf 'ERROR: temporary asset container cleanup leaked into online output\n' >&2
	exit 1
fi
failed_prepare_log="${tmp}/prepare-failed.log"
if PATH="${fake_bin}:${PATH}" HFL_TEST_VERSION=1.2.3 \
	HFL_ONLINE_NATIVE_PROGRESS=1 HFL_TEST_DOCKER_PULL_FAIL=1 \
	python3 "${online}/prepare.py" \
		--source-root "${ROOT}" \
		--version v1.2.3 \
		--region global \
		--output "${tmp}/failed-candidate" \
		>"${failed_prepare_log}" 2>&1; then
	printf 'ERROR: failed Docker pulls unexpectedly prepared an online package\n' >&2
	exit 1
fi
grep -Fq 'registry rejected test image: access denied' "${failed_prepare_log}"
grep -Fq 'docker.io/oneprolabs/hyperfilelens-backend:1.2.3' "${failed_prepare_log}"
grep -Fq 'registry.cn-beijing.aliyuncs.com/oneprolabs/hyperfilelens-backend:1.2.3' \
	"${failed_prepare_log}"
[[ -r "${candidate}/payload/runtime/compose-runtime.sh" ]]
[[ ! -x "${candidate}/payload/runtime/compose-runtime.sh" ]]
"${candidate}/install.sh" --help >/dev/null
"${candidate}/sourcelens/install.sh" --help >/dev/null

python3 - "${candidate}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["edition"] == "community"
assert manifest["image_version"] == "1.2.3"
assert manifest["runtime_images"] == {
    "backend": "hyperfilelens-backend:1.2.3",
    "frontend": "hyperfilelens-frontend:1.2.3",
}
assets = manifest["delivery"]["asset_images"]
assert {entry["local_ref"] for entry in assets} == {
    "hyperfilelens-agent-assets:1.2.3",
    "hyperfilelens-gateway-assets:1.2.3",
    "hyperfilelens-language-assets:1.2.3",
}
assert all(not entry["local_ref"].endswith("-ee") for entry in assets)
PY

metadata="${tmp}/public-metadata"
python3 - "${candidate}" "${metadata}" <<'PY'
import json
import pathlib
import sys

candidate = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
target.mkdir()
manifest = json.loads((candidate / "MANIFEST.json").read_text(encoding="utf-8"))
sourcelens = json.loads(
    (candidate / "sourcelens/BUILD_INFO.json").read_text(encoding="utf-8")
)
entries = [
    *(manifest["delivery"]["registry_images"]),
    *(manifest["delivery"]["asset_images"]),
]
for entry in entries:
    metadata = dict(entry)
    component = metadata["component"]
    if component in {"hfl-backend", "hfl-frontend"}:
        component = f"community-{component}"
        metadata["component"] = component
    if component in {
        "sourcelens-backend",
        "sourcelens-frontend",
        "sourcelens-lensnode",
    }:
        metadata.update(
            sourcelens_version=sourcelens["version"],
            sourcelens_git_ref=sourcelens["git_ref"],
            sourcelens_git_commit=sourcelens["git_commit"],
        )
    (target / f"{component}.json").write_text(
        json.dumps(metadata) + "\n", encoding="utf-8"
    )
PY
PATH="${fake_bin}:${PATH}" "${online}/verify-public-images.sh" "${metadata}"

# Source only defines functions because install.sh guards main with BASH_SOURCE.
source "${ROOT}/deploy/installer/install.sh"
ROOT="${candidate}"
preflight_package_layout
validate_publish_artifacts "${candidate}"
preflight_sourcelens_bundle "${candidate}"
