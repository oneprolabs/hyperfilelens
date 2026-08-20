#!/usr/bin/env bash
# Assemble the small registry-backed package consumed by the installed HFL upgrader.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../build.sh
source "${ROOT}/release/build.sh"
# shellcheck source=../../tools/dependencies/versions/runtime-images.env
source "${ROOT}/tools/dependencies/versions/runtime-images.env"

[[ $# -eq 6 ]] || {
	printf 'Usage: %s METADATA_DIR VERSION OSS_COMMIT EE_COMMIT EXTENSIONS OUTPUT\n' "$0" >&2
	exit 2
}

metadata_dir=$1
version=${2#v}
oss_commit=$3
ee_commit=$4
extensions=$5
output=$6
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
[[ "${oss_commit}" =~ ^[0-9a-f]{40}$ && "${ee_commit}" =~ ^[0-9a-f]{40}$ ]]
[[ "${extensions}" =~ ^/opt/hfl/extensions/[a-zA-Z0-9._-]+(,/opt/hfl/extensions/[a-zA-Z0-9._-]+)*$ ]]

for component in \
	hfl-backend hfl-frontend \
	sourcelens-backend sourcelens-frontend sourcelens-lensnode sourcelens-nginx \
	postgres redis; do
	[[ -s "${metadata_dir}/${component}.json" ]] || {
		printf 'ERROR: missing SaaS image metadata: %s\n' "${component}" >&2
		exit 1
	}
done
for asset in agent gateway language; do
	[[ -s "${metadata_dir}/${asset}-assets.json" ]] || {
		printf 'ERROR: missing SaaS asset metadata: %s\n' "${asset}" >&2
		exit 1
	}
done

pkg_name="hyperfilelens-${version}-ee-saas"
pkg_root="${ROOT}/build/release/staging/${pkg_name}"
images_dir="${pkg_root}/images"
safe_assert_staging_pkg_root "${pkg_root}" "${ROOT}/build/release/staging"
safe_rm_dir "${pkg_root}"
mkdir -p \
	"${images_dir}" \
	"${pkg_root}/payload/media" \
	"${pkg_root}/payload/language-packs" \
	"${pkg_root}/deploy/nginx/certs" \
	"${pkg_root}/deploy/nginx/snippets" \
	"${pkg_root}/deploy/blue-green" \
	"${pkg_root}/deploy/logrotate"

pull_and_tag() {
	local metadata=$1 source_ref digest local_ref immutable_ref
	source_ref="$(jq -r '.sources[] | select(.region == "global") | .ref' "${metadata}")"
	digest="$(jq -r '.digest' "${metadata}")"
	local_ref="$(jq -r '.local_ref' "${metadata}")"
	immutable_ref="${source_ref%:*}@${digest}"
	docker pull --platform linux/amd64 "${immutable_ref}"
	docker tag "${immutable_ref}" "${local_ref}"
}

for component in sourcelens-backend sourcelens-frontend sourcelens-lensnode; do
	pull_and_tag "${metadata_dir}/${component}.json"
done
docker pull --platform linux/amd64 "${NGINX_IMAGE}"
docker tag "${NGINX_IMAGE}" nginx:stable-alpine

export SOURCELENS_HFL_VERSION="${version}"
SOURCELENS_DISTRIBUTION_TAG_OVERRIDE="${version}" \
	BUILD_SOURCELENS=1 "${ROOT}/release/build-sourcelens.sh" \
	--pkg-root "${pkg_root}" \
	--images-dir "${images_dir}" \
	--prebuilt \
	--runtime-only

printf '%s\n' "${version}" >"${pkg_root}/VERSION"
cp "${ROOT}/deploy/docker-compose.yml" "${pkg_root}/docker-compose.yml"
HFL_RELEASE_EDITION=enterprise \
	HFL_IMAGE_VERSION="${version}-ee" \
	HFL_VERSION="${version}" \
	HFL_EXTENSIONS_RUNTIME="${extensions}" \
	stage_release_env_example "${pkg_root}"
stage_default_tls_bundle "${pkg_root}"
cp "${ROOT}/deploy/nginx/default.conf" "${pkg_root}/deploy/nginx/default.conf"
cp "${ROOT}/deploy/nginx/web.conf" "${pkg_root}/deploy/nginx/web.conf"
rsync -a "${ROOT}/deploy/nginx/snippets/" "${pkg_root}/deploy/nginx/snippets/"
cp "${ROOT}/deploy/blue-green/active-color" "${pkg_root}/deploy/blue-green/active-color"
cp "${ROOT}/deploy/logrotate/hyperfilelens.conf" "${pkg_root}/deploy/logrotate/hyperfilelens.conf"
cp "${ROOT}/deploy/installer/install.sh" "${pkg_root}/install.sh"
cp "${ROOT}/deploy/installer/apply-runtime-config.py" "${pkg_root}/apply-runtime-config.py"
cp "${ROOT}/tools/config/sync_env.py" "${pkg_root}/sync-env.py"
cp "${ROOT}/LICENSE" "${pkg_root}/LICENSE"
chmod 755 "${pkg_root}/install.sh" "${pkg_root}/apply-runtime-config.py" "${pkg_root}/sync-env.py"

python3 - "${pkg_root}" "${metadata_dir}" "${version}" "${oss_commit}" "${ee_commit}" <<'PY'
import datetime
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
metadata_dir = pathlib.Path(sys.argv[2])
version, oss_commit, ee_commit = sys.argv[3:6]


def read(name: str) -> dict:
    return json.loads((metadata_dir / f"{name}.json").read_text(encoding="utf-8"))


runtime = [
    read("hfl-backend"),
    read("hfl-frontend"),
    read("sourcelens-backend"),
    read("sourcelens-frontend"),
    read("sourcelens-lensnode"),
    read("sourcelens-nginx"),
    read("postgres"),
    read("redis"),
]
assets = [read("agent-assets"), read("gateway-assets"), read("language-assets")]
by_component = {entry["component"]: entry for entry in runtime}
images = [
    {
        "role": "hyperfilelens",
        "refs": [
            by_component["hfl-backend"]["local_ref"],
            by_component["hfl-frontend"]["local_ref"],
        ],
        "digests": [
            by_component["hfl-backend"]["digest"],
            by_component["hfl-frontend"]["digest"],
        ],
    },
    *[
        {
            "role": entry["role"],
            "refs": [entry["local_ref"]],
            "digests": [entry["digest"]],
        }
        for entry in runtime
        if entry["component"].startswith("sourcelens-")
    ],
    *[
        {
            "role": entry["role"],
            "refs": [entry["local_ref"]],
            "digests": [entry["digest"]],
        }
        for entry in runtime
        if entry["component"] in {"postgres", "redis"}
    ],
]
sourcelens = json.loads((root / "sourcelens/BUILD_INFO.json").read_text(encoding="utf-8"))
manifest = {
    "schema_version": 3,
    "product": "hyperfilelens",
    "edition": "enterprise",
    "channel": "release",
    "artifact_id": f"v{version}",
    "version": version,
    "image_version": f"{version}-ee",
    "built_at": datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "minimum_upgrade_version": "0.1.34",
    "git_commit": oss_commit,
    "extension_commit": ee_commit,
    "runtime_images": {
        "backend": by_component["hfl-backend"]["local_ref"],
        "frontend": by_component["hfl-frontend"]["local_ref"],
    },
    "host_runtime": {
        "os_id": "ubuntu",
        "os_versions": ["20.04", "22.04", "24.04"],
        "arch": "amd64",
        "docker": {"min_engine_version": "24.0.0", "min_compose_version": "2.20.0"},
    },
    "sourcelens": sourcelens,
    "images": images,
    "delivery": {
        "mode": "registry",
        "registry_images": runtime,
        "asset_images": assets,
    },
    "artifacts": {"agent_version": version},
}
(root / "MANIFEST.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

normalize_release_permissions "${pkg_root}"
mkdir -p "$(dirname "${output}")"
tar_tmp="${output}.part"
rm -f "${tar_tmp}"
tar_create_gz "${tar_tmp}" "$(dirname "${pkg_root}")" "${pkg_name}"
mv "${tar_tmp}" "${output}"
sha256sum "${output}" >"${output}.sha256"
