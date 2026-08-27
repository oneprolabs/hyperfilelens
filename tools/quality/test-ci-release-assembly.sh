#!/usr/bin/env bash
# Exercise CI release assembly with tiny synthetic, non-Docker bundles.
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
version="${HFL_TEST_VERSION:-1.2.3}"
edition="${HFL_TEST_EDITION:-community}"
image_version="${version}"
[[ "${edition}" == community ]] || image_version="${version}-ee"
commit="0123456789abcdef0123456789abcdef01234567"
tmp="$(mktemp -d "${ROOT}/build/ci-assembly-test.XXXXXX")"
trap 'rm -rf "${tmp}"' EXIT
input="${tmp}/input"
fixtures="${tmp}/fixtures"
output="${tmp}/release"
mkdir -p "${input}" "${fixtures}"

make_gzip() {
	local output_path=$1 content=$2
	mkdir -p "$(dirname "${output_path}")"
	printf '%s\n' "${content}" | gzip -c >"${output_path}"
}

make_metadata() {
	local output_path=$1 component=$2 ref=$3 digest_char=$4
	mkdir -p "$(dirname "${output_path}")"
	printf '{"component":"%s","ref":"%s","digest":"sha256:%064d","platform":"linux/amd64"}\n' \
		"${component}" "${ref}" "${digest_char}" >"${output_path}"
}

hfl="${fixtures}/hfl"
make_gzip "${hfl}/images/00-hyperfilelens.tar.gz" hfl-images
make_metadata "${hfl}/metadata/hfl-backend.json" hfl-backend "example/hfl-backend:${image_version}" 1
make_metadata "${hfl}/metadata/hfl-frontend.json" hfl-frontend "example/hfl-frontend:${image_version}" 2
tar -C "${hfl}" -cf "${input}/_internal-hfl-images.tar" images metadata

runtime="${fixtures}/runtime"
make_gzip "${runtime}/images/01-postgres-17.tar.gz" postgres
make_gzip "${runtime}/images/02-redis-alpine.tar.gz" redis
make_metadata "${runtime}/metadata/postgres.json" postgres hyperfilelens-postgres:17 3
make_metadata "${runtime}/metadata/redis.json" redis hyperfilelens-redis:alpine 4
tar -C "${runtime}" -cf "${input}/_internal-runtime-images.tar" images metadata

language_packs="${fixtures}/language-packs"
mkdir -p "${language_packs}/payload/language-packs"
for pack_id in zh-hans es; do
LANGUAGE_PACK_ARCHIVE="${language_packs}/payload/language-packs/hyperfilelens-lang-${pack_id}-${version}.tar.gz" \
	LANGUAGE_PACK_VERSION="${version}" LANGUAGE_PACK_ID="${pack_id}" python3 - <<'PY'
import io
import json
import os
import pathlib
import tarfile

archive = pathlib.Path(os.environ["LANGUAGE_PACK_ARCHIVE"])
version = os.environ["LANGUAGE_PACK_VERSION"]
pack_id = os.environ["LANGUAGE_PACK_ID"]
display_name = "Simplified Chinese" if pack_id == "zh-hans" else "Español"
backend_locale = "zh_Hans" if pack_id == "zh-hans" else "es"
aliases = ["zh", "zh-cn"] if pack_id == "zh-hans" else ["es-es", "es-mx"]
component_locale = "zh-cn" if pack_id == "zh-hans" else "es"
files = {
    "manifest.json": json.dumps(
        {
            "schema": 2,
            "id": pack_id,
            "display_name": display_name,
            "version": version,
            "compatible_app": f"=={version}",
            "frontend_code": pack_id,
            "backend_code": pack_id,
            "aliases": aliases,
            "component_locale": component_locale,
        }
    ).encode(),
    "frontend/messages.json": b"{}\n",
    "frontend/element-plus.json": b"{}\n",
    f"backend/locale/{backend_locale}/LC_MESSAGES/django.mo": b"compiled-catalog",
}
with tarfile.open(archive, "w:gz") as package:
    for name, content in files.items():
        member = tarfile.TarInfo(name)
        member.size = len(content)
        member.mode = 0o644
        package.addfile(member, io.BytesIO(content))
PY
done
tar -C "${language_packs}" -cf "${input}/_internal-language-packs.tar" payload

sl="${fixtures}/sourcelens"
make_gzip "${sl}/images/10-sourcelens-app.tar.gz" sourcelens-app
make_gzip "${sl}/images/11-sourcelens-lensnode.tar.gz" sourcelens-lensnode
make_gzip "${sl}/images/12-nginx-stable-alpine.tar.gz" nginx
mkdir -p "${sl}/sourcelens/deploy/postgresql/initdb.d" \
	"${sl}/sourcelens/deploy/nginx/certs" \
	"${sl}/sourcelens/deploy/nginx/hfl-maintenance" \
	"${sl}/sourcelens/deploy/sentry" \
	"${sl}/payload/media/gateway-bootstrap"
printf '#!/usr/bin/env bash\nexit 0\n' >"${sl}/sourcelens/install.sh"
cp "${ROOT}/deploy/installer/sourcelens/compose-lifecycle.sh" \
	"${sl}/sourcelens/compose-lifecycle.sh"
printf '#!/usr/bin/env python3\n' >"${sl}/sourcelens/patch-env-runtime.py"
printf '#!/usr/bin/env python3\n' >"${sl}/sourcelens/sync-sentry-runtime.py"
mkdir -p "${sl}/sourcelens/deploy/nginx"
printf '/* fixture */\n' >"${sl}/sourcelens/deploy/nginx/hfl-sentry-loader.js"
printf 'server { listen 443 ssl; }\n' >"${sl}/sourcelens/deploy/nginx/default.conf"
cp "${ROOT}/deploy/installer/sourcelens/run-creation-gate-off.conf" \
	"${sl}/sourcelens/deploy/nginx/hfl-maintenance/run-creation-gate.conf"
printf '# fixture\n' >"${sl}/sourcelens/deploy/sentry/hfl-sentry-sitecustomize.py"
printf 'services: {}\n' >"${sl}/sourcelens/docker-compose.yml"
printf 'DJANGO_DEBUG=true\n' >"${sl}/sourcelens/.env.example"
printf 'fixture\n' >"${sl}/sourcelens/deploy/postgresql/initdb.d/fixture.sh"
cp "${sl}/images/11-sourcelens-lensnode.tar.gz" \
	"${sl}/payload/media/gateway-bootstrap/lensnode-image-linux-amd64.tar.gz"
cat >"${sl}/sourcelens/BUILD_INFO.json" <<JSON
{
  "enabled": true,
  "git_url": "https://github.com/oneprolabs/sourcelens.git",
  "git_ref": "v0.20.0",
  "git_commit": "0000000000000000000000000000000000000000",
  "git_commit_short": "0000000",
  "version": "0.20.0",
  "patchset_sha256": "fixture",
  "patches": [],
  "build_adapter_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "build_compose_file": "docker-compose.standalone.yml",
  "network": "hyperfilelens-bridge",
  "install_dir": "/opt/hyperfilelens/sourcelens",
  "lensnode_image": "hyperfilelens-sourcelens-lensnode:latest",
  "images": {
    "backend": {"ref": "hyperfilelens-sourcelens-backend:${version}-sl0.20.0", "upstream_ref": "example/backend:v0.20.0", "digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111"},
    "frontend": {"ref": "hyperfilelens-sourcelens-frontend:${version}-sl0.20.0", "upstream_ref": "example/frontend:v0.20.0", "digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222"},
    "lensnode": {"ref": "hyperfilelens-sourcelens-lensnode:${version}-sl0.20.0", "upstream_ref": "example/lensnode:v0.20.0", "digest": "sha256:3333333333333333333333333333333333333333333333333333333333333333"},
    "nginx": {"ref": "hyperfilelens-sourcelens-nginx:stable-alpine", "digest": "sha256:4444444444444444444444444444444444444444444444444444444444444444"}
  }
}
JSON
tar -C "${sl}" -cf "${input}/_internal-sourcelens-bundle.tar" images sourcelens payload

for release_id in 2004 2204 2404; do
	host="${fixtures}/host-${release_id}"
	debs="${fixtures}/debs-${release_id}"
	mkdir -p "${host}/payload/media/gateway-bootstrap"
	mkdir -p "${debs}"
	printf 'synthetic deb\n' >"${debs}/docker-ce_fixture_amd64.deb"
	cat >"${debs}/MANIFEST.json" <<JSON
{"platform":"ubuntu-${release_id}-amd64","versions":{"engine":"fixture","compose":"fixture","min_engine":"24.0.0","min_compose":"2.20.0"},"packages":[]}
JSON
	tar -C "${debs}" -czf \
		"${host}/payload/media/gateway-bootstrap/docker-debs-ubuntu${release_id}-amd64.tar.gz" .
	tar -C "${host}" -cf "${input}/_internal-host-debs-ubuntu${release_id}.tar" payload
done

for asset in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64; do
	agent="${fixtures}/agent-${asset}"
	mkdir -p "${agent}/payload/media/agent-releases/${version}" \
		"${agent}/payload/media/enroll-bootstrap/${version}"
	printf '%s\n' "${asset}" >"${agent}/payload/media/agent-releases/${version}/${asset}.fixture"
	if [[ "${asset}" == "linux-amd64" ]]; then
		printf 'ubuntu 20.04 fixture\n' \
			>"${agent}/payload/media/agent-releases/${version}/hfl-agent-${version}-linux-amd64-ubuntu2004.tar.gz"
		printf 'ubuntu 22.04 fixture\n' \
			>"${agent}/payload/media/agent-releases/${version}/hfl-agent-${version}-linux-amd64-ubuntu2204.tar.gz"
		printf 'ubuntu 24.04 fixture\n' \
			>"${agent}/payload/media/agent-releases/${version}/hfl-agent-${version}-linux-amd64-ubuntu2404.tar.gz"
	fi
	printf '%s\n' "${asset}" >"${agent}/payload/media/enroll-bootstrap/${asset}.fixture"
	ASSET_VALUE="${asset}" OUTPUT_VALUE="${agent}/payload/media/enroll-bootstrap/${version}" python3 - <<'PY'
import os
import pathlib
import tarfile
import zipfile

asset = os.environ["ASSET_VALUE"]
output = pathlib.Path(os.environ["OUTPUT_VALUE"])
platform, arch = asset.split("-", 1)
binary = output / ("hfl-enroll.exe" if platform == "windows" else "hfl-enroll")
binary.write_bytes((asset + "\n").encode())
binary.chmod(0o755)
if platform == "windows":
    archive = output / f"hfl-installer-{asset}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.write(binary, "hfl-enroll.exe")
else:
    archive = output / f"hfl-installer-{asset}.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        info = handle.gettarinfo(str(binary), "hfl-enroll")
        info.mode = 0o755
        with binary.open("rb") as stream:
            handle.addfile(info, stream)
binary.unlink()
PY
	tar -C "${agent}" -cf "${input}/_internal-agent-${asset}.tar" payload
done

HFL_CI_RELEASE_BUILD_DIR="${output}" \
	HFL_RELEASE_MAX_SINGLE_BYTES=1024 \
	HFL_RELEASE_PART_BYTES=4096 \
	HFL_EXTENSION_EXPECTED_COMMIT="$([[ "${edition}" == enterprise ]] && printf '%s' 89abcdef0123456789abcdef0123456789abcdef)" \
	"${ROOT}/release/ci/assemble-release.sh" \
		--input-dir "${input}" \
		--version "${version}" \
		--commit "${commit}" \
		--edition "${edition}"

(
	cd "${output}/dist"
	while IFS= read -r asset; do
		[[ -f "${asset}" ]]
	done <"${output}/release-assets.txt"
	if grep -F 'release-assets.txt' "${output}/release-assets.txt" >/dev/null; then
		printf 'ERROR: internal release asset list must not publish itself\n' >&2
		exit 1
	fi
	jq -e '.spdxVersion == "SPDX-2.3" and (.packages | length) == 7' \
		SBOM.spdx.json >/dev/null
	jq -e '(.files | length) == 3' SBOM.spdx.json >/dev/null
	if [[ "${version}" == main-* ]]; then
		jq -e --arg id "${version}" \
			'.channel == "main" and .artifact_id == $id and (has("version") | not)' \
			MANIFEST.json >/dev/null
	else
		if [[ "${edition}" == enterprise ]]; then
			jq -e --arg version "${version}" \
				'.channel == "release" and .artifact_id == ("v" + $version) and .version == $version and .edition == "enterprise" and .image_version == ($version + "-ee") and .runtime_images.backend == ("hyperfilelens-backend:" + $version + "-ee") and .runtime_images.frontend == ("hyperfilelens-frontend:" + $version + "-ee") and .minimum_upgrade_version == "0.1.34" and .extension_commit == "89abcdef0123456789abcdef0123456789abcdef"' \
				MANIFEST.json >/dev/null
		else
			jq -e --arg version "${version}" \
				'.channel == "release" and .artifact_id == ("v" + $version) and .version == $version and .edition == "community" and .image_version == $version and .runtime_images.backend == ("hyperfilelens-backend:" + $version) and .runtime_images.frontend == ("hyperfilelens-frontend:" + $version) and .minimum_upgrade_version == "0.1.34" and (has("extension_commit") | not)' \
				MANIFEST.json >/dev/null
		fi
	fi
	jq -e --arg version "${version}" \
		'(.language_packs | sort_by(.id)) as $packs | ($packs | length) == 2 and $packs[0].id == "es" and $packs[0].display_name == "Español" and $packs[0].file == ("payload/language-packs/hyperfilelens-lang-es-" + $version + ".tar.gz") and $packs[1].id == "zh-hans" and $packs[1].display_name == "Simplified Chinese" and $packs[1].file == ("payload/language-packs/hyperfilelens-lang-zh-hans-" + $version + ".tar.gz") and all($packs[]; .version == $version and (.size > 0) and (.sha256 | test("^[0-9a-f]{64}$")))' \
		MANIFEST.json >/dev/null
	sha256sum -c SHA256SUMS
	[[ -s hyperfilelens-root-ca.crt ]]
	first="$(find . -maxdepth 1 -type f -name 'hyperfilelens-*.tar.gz.part-000' -print -quit)"
	[[ -n "${first}" ]]
	archive="${first%.part-000}"
	cat "${archive}.part-"* >"${archive}"
	env_example="$(tar -xOzf "${archive}" --wildcards '*/.env.example')"
	grep -Fx "HFL_PRODUCT_VERSION=${version}" <<<"${env_example}" >/dev/null
	grep -Fx "HFL_EDITION=${edition}" <<<"${env_example}" >/dev/null
	image_suffix=""
	[[ "${edition}" == enterprise ]] && image_suffix="-ee"
	grep -Fx "APP_VERSION=${version}${image_suffix}" <<<"${env_example}" >/dev/null
	grep -Fx "HFL_BACKEND_IMAGE=hyperfilelens-backend:${version}${image_suffix}" \
		<<<"${env_example}" >/dev/null
	grep -Fx "HFL_FRONTEND_IMAGE=hyperfilelens-frontend:${version}${image_suffix}" \
		<<<"${env_example}" >/dev/null
	tar -tzf "${archive}" | grep -E '/sync-env\.py$' >/dev/null
	tar -tzf "${archive}" | grep -E '/apply-runtime-config\.py$' >/dev/null
	tar -tzf "${archive}" | grep -E '/deploy/nginx/certs/tls\.crt$' >/dev/null
	tar -tzf "${archive}" | grep -E '/deploy/nginx/certs/tls\.key$' >/dev/null
	tar -tzf "${archive}" | grep -E '/deploy/nginx/certs/root-ca\.crt$' >/dev/null
	tar -tzf "${archive}" | grep -E '/deploy/nginx/web\.conf$' >/dev/null
	tar -tzf "${archive}" | grep -F "/payload/language-packs/hyperfilelens-lang-zh-hans-${version}.tar.gz" >/dev/null
	tar -tzf "${archive}" | grep -F "/payload/language-packs/hyperfilelens-lang-es-${version}.tar.gz" >/dev/null
	key_mode="$(tar -tvzf "${archive}" | awk '$NF ~ /\/deploy\/nginx\/certs\/tls\.key$/ {mode=$1} END {print mode}')"
	[[ "${key_mode}" == "-rw-------" ]]
	tar -tzf "${archive}" | grep -F "/hfl-agent-${version}-linux-amd64-ubuntu2004.tar.gz" >/dev/null
	tar -tzf "${archive}" | grep -F "/hfl-agent-${version}-linux-amd64-ubuntu2204.tar.gz" >/dev/null
	tar -tzf "${archive}" | grep -F "/hfl-agent-${version}-linux-amd64-ubuntu2404.tar.gz" >/dev/null
	"${ROOT}/release/ci/verify-release.sh" --archive "$(realpath "${archive}")"
)

printf 'Synthetic CI release assembly passed for %s.\n' "${version}"
