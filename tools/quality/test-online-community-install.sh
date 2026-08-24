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
grep -Fq -- '--download-source auto|github|gitee' <<<"${help_output}"
grep -Fq -- '--yes' <<<"${help_output}"
grep -Fq 'https://codeload.github.com/oneprolabs/hyperfilelens/tar.gz/refs/tags/${TAG}' \
	"${online}/install.sh"
grep -Fq 'https://gitee.com/oneprolabs/hyperfilelens/repository/archive/${TAG}.tar.gz' \
	"${online}/install.sh"
grep -Fq 'sources=(gitee github)' "${online}/install.sh"
grep -Fq 'sources=(github gitee)' "${online}/install.sh"
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
if grep -Eq '104(42|43|44|45|46)' "${online}/sourcelens/env.example"; then
	printf 'ERROR: online SourceLens template references a legacy HFL public port\n' >&2
	exit 1
fi

(
	# shellcheck source=../../tools/sourcelens/common.sh
	source "${ROOT}/tools/sourcelens/common.sh"
	sourcelens_load_config
	SOURCELENS_GIT_REF=v0.40.0
	SOURCELENS_HFL_VERSION=1.2.3
	sourcelens_resolve_version
	[[ "${SOURCELENS_DISTRIBUTION_TAG}" == 1.2.3-sl0.40.0 ]]
)
(
	# shellcheck source=../../tools/sourcelens/common.sh
	source "${ROOT}/tools/sourcelens/common.sh"
	sourcelens_load_config
	SOURCELENS_GIT_REF=v0.40.0
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
cat >"${fake_bin}/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
digest="sha256:$(printf 'b%.0s' {1..64})"
revision="$(printf 'c%.0s' {1..40})"
case "${1:-} ${2:-}" in
"pull --platform")
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
"tag "* | "rm -f" | "image rm")
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
chmod 755 "${fake_bin}/docker"

PATH="${fake_bin}:${PATH}" HFL_TEST_VERSION=1.2.3 \
	python3 "${online}/prepare.py" \
		--source-root "${ROOT}" \
		--version v1.2.3 \
		--region global \
		--output "${candidate}"
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
