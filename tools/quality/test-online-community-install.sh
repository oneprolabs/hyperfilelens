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
grep -Fq 'https://mirrors.aliyun.com/docker-ce/linux/ubuntu' "${online}/install.sh"
grep -Fq 'https://download.docker.com/linux/ubuntu' "${online}/install.sh"
grep -Fq '9DC858229FC7DD38854AE2D88D81803C0EBFCD88' "${online}/install.sh"
grep -Fq 'ensure_online_docker_runtime' "${online}/install.sh"
grep -Fq 'load_docker_runtime_contract' "${online}/install.sh"
grep -Fq 'Releases published before the per-OS contract' "${online}/install.sh"
grep -Fq 'assert_docker_service_manager' "${online}/install.sh"
grep -Fq 'DOCKER_PACKAGE_INSTALL_ATTEMPTED' "${online}/install.sh"
grep -Fq 'COMPOSE_PACKAGE_INSTALL_ATTEMPTED' "${online}/install.sh"
grep -Fq 'validate_compose_only_install_plan' "${online}/install.sh"
grep -Fq 'selected_docker_apt_source_present' "${online}/install.sh"
grep -Fq 'foreign_docker_runtime_present' "${online}/install.sh"
grep -Fq 'docker_ce_runtime_present' "${online}/install.sh"
grep -Fq 'docker_apt_source_present' "${online}/install.sh"
grep -Fq 'Acquire::Retries=3' "${online}/install.sh"
grep -Fq 'Acquire::http::Timeout=60' "${online}/install.sh"
grep -Fq 'Acquire::https::Timeout=60' "${online}/install.sh"
grep -Fq 'DPkg::Lock::Timeout=120' "${online}/install.sh"
grep -Fq 'apt_install_with_network_retry' "${online}/install.sh"
grep -Fq 'apt_failure_is_transient' "${online}/install.sh"
grep -Fq 'dpkg_state_clean_for_retry' "${online}/install.sh"
grep -Fq 'preserve_apt_failure_log' "${online}/install.sh"
grep -Fq -- '--no-upgrade' "${online}/install.sh"
for package in docker-ce docker-ce-cli containerd.io docker-compose-plugin; do
	grep -Fq "${package}" "${online}/install.sh"
done
if grep -Eq 'def parse\([^)]*\)[[:space:]]*->[[:space:]]*(tuple|list|dict)\[' \
	"${online}/install.sh"; then
	printf 'ERROR: online installer uses a Python annotation unsupported by Ubuntu 20.04\n' >&2
	exit 1
fi
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
"info ")
	[[ "${HFL_TEST_DOCKER_INFO_FAIL:-0}" != "1" ]]
	;;
"version --format")
	printf '%s\n' "${HFL_TEST_DOCKER_ENGINE_VERSION:-29.6.1}"
	;;
"compose version")
	[[ "${HFL_TEST_DOCKER_COMPOSE_MISSING:-0}" != "1" ]] || exit 1
	printf '%s\n' "${HFL_TEST_DOCKER_COMPOSE_VERSION:-2.39.1}"
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
cat >"${fake_bin}/dpkg-query" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${*: -1}" == docker-ce || "${*: -1}" == docker-ce-cli ]]; then
	printf 'ii '
	exit 0
fi
exit 1
SH
chmod 755 "${fake_bin}/dpkg-query"

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

apt_retry_log="${tmp}/apt-retry.log"
apt_retry_saved="${tmp}/logs/install-test-apt.log"
mkdir -p "$(dirname "${apt_retry_saved}")"
(
	# A transient package download failure retries once after a clean dpkg audit.
	# shellcheck disable=SC1090
	source "${online_functions}"
	ONLINE_LOG_FILE="${tmp}/logs/install-test.log"
	attempts=0
	apt-get() {
		attempts=$((attempts + 1))
		if ((attempts == 1)); then
			printf 'E: Failed to fetch https://mirror.example/docker-ce-cli.deb (Connection timed out)\n'
			return 100
		fi
		return 0
	}
	dpkg_state_clean_for_retry() { return 0; }
	sleep() { :; }
	apt_install_with_network_retry "${apt_retry_log}" install docker-ce
	[[ "${attempts}" -eq 2 ]]
	[[ ! -e "${apt_retry_saved}" ]]
)
(
	# A non-transient package failure does not retry and keeps its full output.
	# shellcheck disable=SC1090
	source "${online_functions}"
	ONLINE_LOG_FILE="${tmp}/logs/install-test.log"
	apt-get() { printf 'E: Unable to locate package docker-ce\n'; return 100; }
	if apt_install_with_network_retry "${apt_retry_log}" install docker-ce; then
		exit 1
	fi
	cmp -s "${apt_retry_log}" "${apt_retry_saved}"
)
(
	# A second transient failure is reported as retryable only when dpkg remains clean.
	# shellcheck disable=SC1090
	source "${online_functions}"
	ONLINE_LOG_FILE="${tmp}/logs/final.log"
	final_log="${tmp}/apt-final.log"
	final_saved="${tmp}/logs/final-apt.log"
	apt-get() { printf 'E: Failed to fetch https://mirror.example/docker-ce-cli.deb (Connection timed out)\n'; return 100; }
	dpkg_state_clean_for_retry() { return 0; }
	sleep() { :; }
	if apt_install_with_network_retry "${final_log}" install docker-ce; then
		exit 1
	fi
	[[ "${APT_FAILURE_DPKG_CLEAN}" -eq 1 ]]
	cmp -s "${final_log}" "${final_saved}"
)

(
	# shellcheck disable=SC1090
	source "${online_functions}"
	docker_version_ge 24.0 24.0.0
	! docker_version_ge 23.9.9 24.0.0
	MIRROR=cn
	configure_mirror
	[[ "${DOCKER_CE_APT_BASE}" == "https://mirrors.aliyun.com/docker-ce/linux/ubuntu" ]]
	[[ "${DOCKER_CE_GPG_URL}" == "https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg" ]]
)
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	MIRROR=global
	configure_mirror
	[[ "${DOCKER_CE_APT_BASE}" == "https://download.docker.com/linux/ubuntu" ]]
	[[ "${DOCKER_CE_GPG_URL}" == "https://download.docker.com/linux/ubuntu/gpg" ]]
)
apt_source_fixture="${tmp}/apt-source-fixture"
mkdir -p "${apt_source_fixture}/sources.list.d"
printf '# deb https://download.docker.com/linux/ubuntu noble stable\n' \
	>"${apt_source_fixture}/sources.list.d/docker.list"
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://download.docker.com/linux/ubuntu
	! selected_docker_apt_source_present "${apt_source_fixture}"
)
printf 'deb https://example.test/ubuntu noble main # https://download.docker.com/linux/ubuntu\n' \
	>"${apt_source_fixture}/sources.list.d/docker.list"
(
	# A Docker URL in a one-line source comment is not an active package source.
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://download.docker.com/linux/ubuntu
	! selected_docker_apt_source_present "${apt_source_fixture}"
	! docker_apt_source_present "${apt_source_fixture}"
)
printf 'deb [arch=amd64] https://download.docker.com/linux/ubuntu noble stable\n' \
	>"${apt_source_fixture}/sources.list.d/docker.list"
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://download.docker.com/linux/ubuntu
	selected_docker_apt_source_present "${apt_source_fixture}"
	DOCKER_CE_SOURCE_NAME='Docker CE test source'
	install_docker_prerequisites() {
		printf 'ERROR: existing Docker source unexpectedly installed prerequisites\n' >&2
		return 1
	}
	configure_docker_apt_source() {
		printf 'ERROR: existing Docker source was configured again\n' >&2
		return 1
	}
	ensure_docker_apt_source "${apt_source_fixture}"
)
cat >"${apt_source_fixture}/sources.list.d/docker.sources" <<'SOURCES'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Enabled: no
SOURCES
rm "${apt_source_fixture}/sources.list.d/docker.list"
(
	# A disabled Deb822 stanza is not an enabled Docker CE source.
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://download.docker.com/linux/ubuntu
	! selected_docker_apt_source_present "${apt_source_fixture}"
	! docker_apt_source_present "${apt_source_fixture}"
)
cat >"${apt_source_fixture}/sources.list.d/docker.sources" <<'SOURCES'
Types: deb-src
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable

Types: deb
URIs: https://example.test/ubuntu
Suites: noble
Components: stable
Description: Documentation at https://download.docker.com/linux/ubuntu
SOURCES
(
	# Source-only entries and URLs outside a Deb822 URIs field are not usable
	# Docker CE binary package sources.
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://download.docker.com/linux/ubuntu
	! selected_docker_apt_source_present "${apt_source_fixture}"
	! docker_apt_source_present "${apt_source_fixture}"
)
cat >"${apt_source_fixture}/sources.list.d/docker.sources" <<'SOURCES'
Types: deb deb-src
URIs:
 https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
SOURCES
(
	# Enabled Deb822 binary sources and continuation fields are recognized.
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://download.docker.com/linux/ubuntu
	selected_docker_apt_source_present "${apt_source_fixture}"
	docker_apt_source_present "${apt_source_fixture}"
)
linked_source="${tmp}/linked-docker.sources"
mv "${apt_source_fixture}/sources.list.d/docker.sources" "${linked_source}"
ln -s "${linked_source}" "${apt_source_fixture}/sources.list.d/docker.sources"
(
	# Apt accepts source files through symlinks; an enabled linked source must be
	# reused instead of adding a duplicate HFL-managed source.
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://download.docker.com/linux/ubuntu
	selected_docker_apt_source_present "${apt_source_fixture}"
	docker_apt_source_present "${apt_source_fixture}"
)
runtime_contract_session="${tmp}/runtime-contract"
mkdir -p "${runtime_contract_session}/source/deploy/online"
cp "${online}/docker-ce-versions.env" \
	"${runtime_contract_session}/source/deploy/online/docker-ce-versions.env"
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR="${runtime_contract_session}"
	TAG=v1.2.3
	VERSION_ID=20.04
	load_docker_runtime_contract
	[[ "${DOCKER_ENGINE_PACKAGE_VERSION}" == '5:28.1.1-1~ubuntu.20.04~focal' ]]
	[[ "${DOCKER_CLI_PACKAGE_VERSION}" == '5:28.1.1-1~ubuntu.20.04~focal' ]]
	[[ "${DOCKER_CONTAINERD_PACKAGE_VERSION}" == '1.7.27-1' ]]
	[[ "${DOCKER_COMPOSE_PACKAGE_VERSION}" == '2.35.1-1~ubuntu.20.04~focal' ]]
	[[ "${DOCKER_TARGET_ENGINE_VERSION}" == 28.1.1 ]]
	[[ "${DOCKER_TARGET_COMPOSE_VERSION}" == 2.35.1 ]]
)
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR="${runtime_contract_session}"
	TAG=v1.2.3
	VERSION_ID=22.04
	load_docker_runtime_contract
	[[ "${DOCKER_ENGINE_PACKAGE_VERSION}" == '5:29.2.1-1~ubuntu.22.04~jammy' ]]
	[[ "${DOCKER_CONTAINERD_PACKAGE_VERSION}" == '2.2.1-1~ubuntu.22.04~jammy' ]]
	[[ "${DOCKER_COMPOSE_PACKAGE_VERSION}" == '5.0.2-1~ubuntu.22.04~jammy' ]]
)
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR="${runtime_contract_session}"
	TAG=v1.2.3
	VERSION_ID=24.04
	load_docker_runtime_contract
	[[ "${DOCKER_ENGINE_PACKAGE_VERSION}" == '5:29.2.1-1~ubuntu.24.04~noble' ]]
	[[ "${DOCKER_CONTAINERD_PACKAGE_VERSION}" == '2.2.1-1~ubuntu.24.04~noble' ]]
	[[ "${DOCKER_COMPOSE_PACKAGE_VERSION}" == '5.0.2-1~ubuntu.24.04~noble' ]]
)
low_runtime_contract_session="${tmp}/runtime-contract-below-minimum"
mkdir -p "${low_runtime_contract_session}/source/deploy/online"
sed 's/^UBUNTU2004_ENGINE_VERSION=.*/UBUNTU2004_ENGINE_VERSION=5:23.0.0-1~ubuntu.20.04~focal/' \
	"${online}/docker-ce-versions.env" \
	>"${low_runtime_contract_session}/source/deploy/online/docker-ce-versions.env"
low_runtime_contract_log="${tmp}/runtime-contract-below-minimum.log"
if (
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR="${low_runtime_contract_session}"
	TAG=v1.2.3
	VERSION_ID=20.04
	load_docker_runtime_contract
) >"${low_runtime_contract_log}" 2>&1; then
	printf 'ERROR: online installer accepted a Docker runtime below its minimum version\n' >&2
	exit 1
fi
grep -Fq 'below the required 24.0.0' "${low_runtime_contract_log}"
legacy_runtime_session="${tmp}/legacy-runtime-contract"
mkdir -p "${legacy_runtime_session}/source/deploy/online"
(
	# Older published tags do not contain the per-OS contract. The current
	# bootstrap must keep those tags installable on a clean host.
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR="${legacy_runtime_session}"
	TAG=v0.2.14
	VERSION_ID=20.04
	load_docker_runtime_contract
	[[ "${DOCKER_ENGINE_PACKAGE_VERSION}" == '5:28.1.1-1~ubuntu.20.04~focal' ]]
	[[ "${DOCKER_COMPOSE_PACKAGE_VERSION}" == '2.35.1-1~ubuntu.20.04~focal' ]]
)
(
	# Verify that a genuinely clean host without Docker selects the managed
	# online installation path without touching the test host package state.
	# shellcheck disable=SC1090
	source "${online_functions}"
	command() {
		if [[ "${1:-}" == -v && ( "${2:-}" == docker || "${2:-}" == docker-compose ) ]]; then
			return 1
		fi
		builtin command "$@"
	}
	docker_packages_present() { return 1; }
	docker_residual_state_present() { return 1; }
	foreign_docker_apt_source_present() { return 1; }
	inspect_docker_runtime
	[[ "${DOCKER_RUNTIME_ACTION}" == install ]]
)
(
	# Removed Docker packages with config-files-only (dpkg "rc") state do not
	# represent installed package payloads on an otherwise clean host.
	# shellcheck disable=SC1090
	source "${online_functions}"
	dpkg-query() {
		[[ "${*: -1}" == docker.io ]] || return 1
		printf 'rc '
	}
	! docker_packages_present
)
(
	# A healthy, supported Docker Engine with no Compose V2 plugin selects the
	# narrowly scoped plugin bootstrap instead of changing the Engine runtime.
	# shellcheck disable=SC1090
	source "${online_functions}"
	docker() {
		case "${1:-} ${2:-}" in
		"info ") return 0 ;;
		"version --format") printf '29.2.1\n' ;;
		"compose version") return 1 ;;
		esac
		return 1
	}
	dpkg-query() {
		[[ "${*: -1}" == docker-ce || "${*: -1}" == docker-ce-cli ]] || return 1
		printf 'ii '
	}
	inspect_docker_runtime
	[[ "${DOCKER_RUNTIME_ACTION}" == install-compose ]]
	[[ "${DOCKER_ENGINE_VERSION}" == 29.2.1 ]]
)
(
	# An Engine package without its Docker CE CLI package is not a complete
	# Docker CE runtime that can receive a managed Compose plugin.
	# shellcheck disable=SC1090
	source "${online_functions}"
	dpkg-query() {
		[[ "${*: -1}" == docker-ce ]] || return 1
		printf 'ii '
	}
	! docker_ce_runtime_present
)
foreign_runtime_log="${tmp}/foreign-runtime-compose.log"
if (
	# A Docker runtime supplied by Ubuntu/Moby/Snap must not be converted to
	# Docker CE merely to add Compose V2.
	# shellcheck disable=SC1090
	source "${online_functions}"
	docker() {
		case "${1:-} ${2:-}" in
		"info ") return 0 ;;
		"version --format") printf '29.2.1\n' ;;
		"compose version") return 1 ;;
		esac
		return 1
	}
	dpkg-query() {
		[[ "${*: -1}" == docker.io ]] || return 1
		printf 'ii '
	}
	inspect_docker_runtime
) >"${foreign_runtime_log}" 2>&1; then
	printf 'ERROR: foreign Docker runtime was accepted for Compose-only bootstrap\n' >&2
	exit 1
fi
grep -Fq 'not a Docker CE installation' "${foreign_runtime_log}"
foreign_complete_runtime_log="${tmp}/foreign-complete-runtime.log"
if (
	# A non-Docker-CE runtime is rejected even when it already exposes Compose;
	# the online installer only supports Docker CE for its managed contract.
	# shellcheck disable=SC1090
	source "${online_functions}"
	docker() {
		case "${1:-} ${2:-}" in
		"info ") return 0 ;;
		"version --format") printf '29.2.1\n' ;;
		"compose version") printf '2.39.1\n' ;;
		esac
		return 1
	}
	dpkg-query() {
		[[ "${*: -1}" == docker.io ]] || return 1
		printf 'ii '
	}
	inspect_docker_runtime
) >"${foreign_complete_runtime_log}" 2>&1; then
	printf 'ERROR: complete foreign Docker runtime was accepted\n' >&2
	exit 1
fi
grep -Fq 'not a Docker CE installation' "${foreign_complete_runtime_log}"
unrecognized_runtime_log="${tmp}/unrecognized-runtime-compose.log"
if (
	# A healthy static or otherwise unrecognized Docker binary is not sufficient
	# evidence that the Docker CE apt plugin can be added safely.
	# shellcheck disable=SC1090
	source "${online_functions}"
	docker() {
		case "${1:-} ${2:-}" in
		"info ") return 0 ;;
		"version --format") printf '29.2.1\n' ;;
		"compose version") return 1 ;;
		esac
		return 1
	}
	dpkg-query() { return 1; }
	inspect_docker_runtime
) >"${unrecognized_runtime_log}" 2>&1; then
	printf 'ERROR: unrecognized Docker runtime was accepted for Compose-only bootstrap\n' >&2
	exit 1
fi
grep -Fq 'not a Docker CE installation' "${unrecognized_runtime_log}"
(
	# A removed docker.io package with residual configuration (dpkg "rc") does
	# not identify the active Docker Engine as Ubuntu's runtime.
	# shellcheck disable=SC1090
	source "${online_functions}"
	dpkg-query() {
		[[ "${*: -1}" == docker.io ]] || return 1
		printf 'rc '
	}
	command() {
		if [[ "${1:-}" == -v && "${2:-}" == docker ]]; then
			printf '/usr/bin/docker\n'
			return 0
		fi
		builtin command "$@"
	}
	! foreign_docker_runtime_present
)
compose_target_log="${tmp}/compose-target.log"
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	TAG=v1.2.3
	SOURCE_NAME=GitHub
	REGISTRY_NAME='Docker Hub'
	ONLINE_LOG_FILE=/opt/hyperfilelens/logs/install-test.log
	DOCKER_RUNTIME_ACTION=install-compose
	DOCKER_ENGINE_VERSION=29.2.1
	DOCKER_COMPOSE_PACKAGE_VERSION='5.0.2-1~ubuntu.24.04~noble'
	DOCKER_CE_SOURCE_NAME='Docker CE · https://download.docker.com/linux/ubuntu'
	print_target
) >"${compose_target_log}"
grep -Fq 'Docker Engine  29.2.1 · reuse' "${compose_target_log}"
grep -Fq 'Docker Compose not installed → install docker-compose-plugin 5.0.2-1~ubuntu.24.04~noble' \
	"${compose_target_log}"
grep -Fq 'Install scope  Compose V2 plugin only' "${compose_target_log}"
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	dpkg-query() {
		[[ "${*: -1}" == docker-ce ]] || return 1
		printf 'iU '
	}
	docker_packages_present
)

service_manager_log="${tmp}/docker-service-manager-rejection.log"
if (
	# shellcheck disable=SC1090
	source "${online_functions}"
	command() {
		if [[ "${1:-}" == -v && "${2:-}" == systemctl ]]; then
			return 1
		fi
		builtin command "$@"
	}
	assert_docker_service_manager
) >"${service_manager_log}" 2>&1; then
	printf 'ERROR: online Docker bootstrap accepted a host without systemd\n' >&2
	exit 1
fi
grep -Fq 'systemd is required to install and manage Docker CE automatically' \
	"${service_manager_log}"

residual_runtime_bin="${tmp}/residual-runtime-bin"
mkdir -p "${residual_runtime_bin}"
printf '#!/usr/bin/env bash\nexit 0\n' >"${residual_runtime_bin}/dockerd"
chmod 755 "${residual_runtime_bin}/dockerd"
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	PATH="${residual_runtime_bin}:${PATH}"
	docker_residual_state_present
)

partial_install_log="${tmp}/docker-partial-install-warning.log"
if (
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR=""
	DOCKER_PACKAGE_INSTALL_ATTEMPTED=1
	DOCKER_BOOTSTRAPPED=0
	false || cleanup
) >"${partial_install_log}" 2>&1; then
	printf 'ERROR: partial Docker package installation cleanup returned success\n' >&2
	exit 1
fi
grep -Fq 'may have left partially installed packages' "${partial_install_log}"
grep -Fq 'dpkg --audit' "${partial_install_log}"

partial_compose_log="${tmp}/compose-partial-install-warning.log"
if (
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR=""
	COMPOSE_PACKAGE_INSTALL_ATTEMPTED=1
	COMPOSE_BOOTSTRAPPED=0
	false || cleanup
) >"${partial_compose_log}" 2>&1; then
	printf 'ERROR: partial Compose plugin installation cleanup returned success\n' >&2
	exit 1
fi
grep -Fq 'Compose V2 plugin installation did not complete' "${partial_compose_log}"
grep -Fq 'existing Docker Engine was not replaced' "${partial_compose_log}"

dpkg_audit_error_log="${tmp}/docker-dpkg-audit-error.log"
if (
	# shellcheck disable=SC1090
	source "${online_functions}"
	dpkg() {
		printf 'dpkg database is unavailable\n' >&2
		return 2
	}
	assert_clean_dpkg_state
) >"${dpkg_audit_error_log}" 2>&1; then
	printf 'ERROR: online Docker bootstrap ignored a failed dpkg audit\n' >&2
	exit 1
fi
grep -Fq 'host package state could not be inspected' "${dpkg_audit_error_log}"

safe_apt_plan="${tmp}/docker-safe.plan"
printf '0 upgraded, 5 newly installed, 0 to remove and 0 not upgraded.\n' >"${safe_apt_plan}"
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	validate_apt_install_plan "${safe_apt_plan}"
)
unsafe_apt_plan="${tmp}/docker-unsafe.plan"
printf '1 upgraded, 5 newly installed, 0 to remove and 0 not upgraded.\n' >"${unsafe_apt_plan}"
unsafe_apt_log="${tmp}/docker-unsafe.log"
if (
	# shellcheck disable=SC1090
	source "${online_functions}"
	validate_apt_install_plan "${unsafe_apt_plan}"
) >"${unsafe_apt_log}" 2>&1; then
	printf 'ERROR: online Docker bootstrap accepted a host-package upgrade plan\n' >&2
	exit 1
fi
grep -Fq 'would upgrade, downgrade, or remove existing host packages' "${unsafe_apt_log}"

safe_compose_plan="${tmp}/compose-safe.plan"
cat >"${safe_compose_plan}" <<'PLAN'
Inst docker-compose-plugin (5.0.2-1~ubuntu.24.04~noble Docker CE:stable [amd64])
Conf docker-compose-plugin (5.0.2-1~ubuntu.24.04~noble Docker CE:stable [amd64])
0 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.
PLAN
(
	# shellcheck disable=SC1090
	source "${online_functions}"
	validate_apt_install_plan "${safe_compose_plan}" "Docker Compose V2 installation"
	validate_compose_only_install_plan "${safe_compose_plan}"
)
unsafe_compose_plan="${tmp}/compose-unsafe.plan"
cat >"${unsafe_compose_plan}" <<'PLAN'
Inst docker-ce-cli (5:29.2.1-1~ubuntu.24.04~noble Docker CE:stable [amd64])
Inst docker-compose-plugin (5.0.2-1~ubuntu.24.04~noble Docker CE:stable [amd64])
0 upgraded, 2 newly installed, 0 to remove and 0 not upgraded.
PLAN
unsafe_compose_log="${tmp}/compose-unsafe.log"
if (
	# shellcheck disable=SC1090
	source "${online_functions}"
	validate_compose_only_install_plan "${unsafe_compose_plan}"
) >"${unsafe_compose_log}" 2>&1; then
	printf 'ERROR: Compose-only bootstrap accepted a Docker CLI package change\n' >&2
	exit 1
fi
grep -Fq 'cannot be installed safely without changing the existing Docker runtime' \
	"${unsafe_compose_log}"
empty_compose_plan="${tmp}/compose-empty.plan"
: >"${empty_compose_plan}"
empty_compose_log="${tmp}/compose-empty.log"
if (
	# An empty or unexpected apt plan must fail with the controlled diagnostic,
	# not an unbound-array error under `set -u`.
	# shellcheck disable=SC1090
	source "${online_functions}"
	validate_compose_only_install_plan "${empty_compose_plan}"
) >"${empty_compose_log}" 2>&1; then
	printf 'ERROR: empty Compose-only apt plan was accepted\n' >&2
	exit 1
fi
grep -Fq 'cannot be installed safely without changing the existing Docker runtime' \
	"${empty_compose_log}"

foreign_source_fixture="${tmp}/foreign-apt-source-fixture"
mkdir -p "${foreign_source_fixture}/sources.list.d"
printf 'deb https://download.docker.com/linux/ubuntu noble stable\n' \
	>"${foreign_source_fixture}/sources.list.d/docker.list"
(
	# A different Docker source must not be combined with the selected mirror
	# during Compose-only installation.
	# shellcheck disable=SC1090
	source "${online_functions}"
	DOCKER_CE_APT_BASE=https://mirrors.aliyun.com/docker-ce/linux/ubuntu
	if ! foreign_docker_apt_source_present "${foreign_source_fixture}"; then
		printf 'ERROR: foreign Docker source fixture was not detected\n' >&2
		exit 1
	fi
	docker_apt_source_present "${foreign_source_fixture}"
	DOCKER_CE_SOURCE_NAME='Selected Docker CE test source'
	install_docker_prerequisites() {
		printf 'ERROR: alternate Docker source unexpectedly installed prerequisites\n' >&2
		return 1
	}
	configure_docker_apt_source() {
		printf 'ERROR: alternate Docker source was replaced\n' >&2
		return 1
	}
	ensure_docker_apt_source "${foreign_source_fixture}"
)
printf '# deb https://download.docker.com/linux/ubuntu noble stable\n' \
	>"${foreign_source_fixture}/sources.list.d/docker.list"
(
	# Commented-out Docker sources are not active conflicts.
	# shellcheck disable=SC1090
	source "${online_functions}"
	! foreign_docker_apt_source_present "${foreign_source_fixture}"
	! docker_apt_source_present "${foreign_source_fixture}"
)
cat >"${foreign_source_fixture}/sources.list.d/docker.sources" <<'SOURCES'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Enabled: no
SOURCES
rm "${foreign_source_fixture}/sources.list.d/docker.list"
(
	# Disabled Deb822 Docker sources are not active conflicts.
	# shellcheck disable=SC1090
	source "${online_functions}"
	! foreign_docker_apt_source_present "${foreign_source_fixture}"
	! docker_apt_source_present "${foreign_source_fixture}"
)

bootstrap_apt_log="${tmp}/docker-bootstrap-apt.log"
bootstrap_systemctl_log="${tmp}/docker-bootstrap-systemctl.log"
(
	# Exercise the mutating branch with command fakes: the test must prove the
	# selected package set, no-remove guard, service lifecycle, and post-checks
	# without changing the CI host.
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR="${tmp}/docker-bootstrap"
	mkdir -p "${SESSION_DIR}"
	DOCKER_RUNTIME_ACTION=install
	DOCKER_ENGINE_PACKAGE_VERSION='5:29.2.1-test'
	DOCKER_CLI_PACKAGE_VERSION='5:29.2.1-test'
	DOCKER_CONTAINERD_PACKAGE_VERSION='2.2.1-test'
	DOCKER_COMPOSE_PACKAGE_VERSION='5.0.2-test'
	assert_clean_dpkg_state() { :; }
	install_docker_prerequisites() { :; }
	configure_docker_apt_source() { :; }
	apt-get() {
		printf '%s\n' "$*" >>"${bootstrap_apt_log}"
		if [[ " $* " == *' --simulate '* ]]; then
			printf '0 upgraded, 5 newly installed, 0 to remove and 0 not upgraded.\n'
		fi
	}
	systemctl() {
		printf '%s\n' "$*" >>"${bootstrap_systemctl_log}"
	}
	docker() {
		[[ "${1:-}" == info ]]
	}
	docker_engine_version() { printf '29.6.1'; }
	docker_compose_version() { printf '2.39.1'; }
	sleep() { :; }
	install_online_docker_runtime
	[[ "${DOCKER_BOOTSTRAPPED}" -eq 1 ]]
	[[ "${DOCKER_RUNTIME_ACTION}" == reuse ]]
)
grep -Fq -- '--simulate --no-remove --no-upgrade --no-install-recommends install docker-ce=5:29.2.1-test docker-ce-cli=5:29.2.1-test containerd.io=2.2.1-test docker-compose-plugin=5.0.2-test' \
	"${bootstrap_apt_log}"
grep -Fq -- 'install -y --no-remove --no-upgrade --no-install-recommends docker-ce=5:29.2.1-test docker-ce-cli=5:29.2.1-test containerd.io=2.2.1-test docker-compose-plugin=5.0.2-test' \
	"${bootstrap_apt_log}"
grep -Fxq 'enable --now docker' "${bootstrap_systemctl_log}"
grep -Fxq 'is-active --quiet docker' "${bootstrap_systemctl_log}"
grep -Fxq 'is-enabled --quiet docker' "${bootstrap_systemctl_log}"

compose_apt_log="${tmp}/compose-bootstrap-apt.log"
(
	# Exercise the Compose-only branch with command fakes. Only the pinned
	# plugin package may be planned or installed, and the Engine must not change.
	# shellcheck disable=SC1090
	source "${online_functions}"
	SESSION_DIR="${tmp}/compose-bootstrap"
	mkdir -p "${SESSION_DIR}"
	DOCKER_RUNTIME_ACTION=install-compose
	DOCKER_ENGINE_VERSION=29.2.1
	DOCKER_COMPOSE_PACKAGE_VERSION='5.0.2-test'
	assert_clean_dpkg_state() { :; }
	ensure_docker_apt_source() { :; }
	selected_docker_apt_source_present() { return 0; }
	apt-get() {
		printf '%s\n' "$*" >>"${compose_apt_log}"
		if [[ " $* " == *' --simulate '* ]]; then
			printf 'Inst docker-compose-plugin (5.0.2-test Docker CE:stable [amd64])\n'
			printf 'Conf docker-compose-plugin (5.0.2-test Docker CE:stable [amd64])\n'
			printf '0 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.\n'
		fi
	}
	docker() {
		[[ "${1:-}" == info ]]
	}
	docker_engine_version() { printf '29.2.1'; }
	docker_compose_version() { printf '5.0.2'; }
	install_online_compose_plugin
	[[ "${COMPOSE_BOOTSTRAPPED}" -eq 1 ]]
	[[ "${DOCKER_RUNTIME_ACTION}" == reuse ]]
)
grep -Fq -- '--simulate --no-remove --no-upgrade --no-install-recommends install docker-compose-plugin=5.0.2-test' \
	"${compose_apt_log}"
grep -Fq -- 'install -y --no-remove --no-upgrade --no-install-recommends docker-compose-plugin=5.0.2-test' \
	"${compose_apt_log}"
if grep -Eq 'docker-ce=|docker-ce-cli=|containerd.io=' "${compose_apt_log}"; then
	printf 'ERROR: Compose-only bootstrap attempted to change Docker Engine packages\n' >&2
	exit 1
fi

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
    "\nconfirm_installation\ninstall_host_tools\n": (
        "\nconfirm_installation\n:\n"
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
grep -Fq 'Docker Engine  29.6.1 · reuse' "${latest_log}"
grep -Fq 'Docker Compose 2.39.1 · reuse' "${latest_log}"
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

for runtime_case in daemon engine compose; do
	runtime_log="${tmp}/docker-${runtime_case}-rejection.log"
	case "${runtime_case}" in
	daemon)
		runtime_env=(HFL_TEST_DOCKER_INFO_FAIL=1)
		runtime_message='daemon is unavailable'
		;;
	engine)
		runtime_env=(HFL_TEST_DOCKER_ENGINE_VERSION=23.0.6)
		runtime_message='does not meet the minimum required version 24.0.0'
		;;
	compose)
		runtime_env=(HFL_TEST_DOCKER_COMPOSE_VERSION=2.19.9)
		runtime_message='does not meet the minimum required version 2.20.0'
		;;
	esac
	if env PATH="${fake_bin}:${PATH}" HFL_TEST_TAG_FIXTURE="${tag_fixture}" \
		"${runtime_env[@]}" "${test_installer}" --mirror global --yes \
		>"${runtime_log}" 2>&1; then
		printf 'ERROR: invalid existing Docker runtime was accepted: %s\n' "${runtime_case}" >&2
		exit 1
	fi
	grep -Fq "${runtime_message}" "${runtime_log}" || {
		cat "${runtime_log}" >&2
		exit 1
	}
done

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
