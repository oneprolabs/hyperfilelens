#!/usr/bin/env bash
# Merge CI bundles into one minimal Docker build context used only as an asset carrier.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

[[ $# -eq 4 ]] || {
	printf 'Usage: %s agent|gateway|language INPUT_DIR VERSION OUTPUT_DIR\n' "$0" >&2
	exit 2
}

kind=$1
input_dir=$2
version=${3#v}
output_dir=$4
[[ "${kind}" =~ ^(agent|gateway|language)$ ]]
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
[[ -d "${input_dir}" ]]
rm -rf -- "${output_dir}"
asset_root="${output_dir}/rootfs/opt/hyperfilelens-assets"
mkdir -p "${asset_root}"

extract_matching() {
	local pattern=$1 matched=0 archive
	while IFS= read -r archive; do
		[[ -n "${archive}" ]] || continue
		matched=1
		tar -xf "${archive}" -C "${asset_root}"
	done < <(find "${input_dir}" -type f -name "${pattern}" -print | sort)
	[[ "${matched}" -eq 1 ]] || {
		printf 'ERROR: no input matched %s\n' "${pattern}" >&2
		exit 1
	}
}

case "${kind}" in
agent)
	extract_matching '_internal-agent-*.tar'
	[[ -d "${asset_root}/payload/media/agent-releases/${version}" ]]
	[[ -d "${asset_root}/payload/media/enroll-bootstrap/${version}" ]]
	python3 - "${asset_root}/payload/media/enroll-bootstrap" "${version}" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
artifacts = {}
pattern = re.compile(r"^hfl-installer-(linux|darwin|windows)-(amd64|arm64)\.(tar\.gz|zip)$")
for path in sorted((root / version).glob("hfl-installer-*")):
    match = pattern.fullmatch(path.name)
    if match is None:
        continue
    platform, arch, _ = match.groups()
    artifacts[f"{platform}-{arch}"] = {
        "filename": f"{version}/{path.name}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    }
expected = {"linux-amd64", "linux-arm64", "darwin-amd64", "darwin-arm64", "windows-amd64"}
if set(artifacts) != expected:
    raise SystemExit(f"minimal installer matrix mismatch: {sorted(artifacts)}")
(root / "INSTALLER_MANIFEST.json").write_text(
    json.dumps({"schema_version": 1, "artifacts": artifacts}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
	;;
gateway)
	extract_matching '_internal-host-debs-*.tar'
	gateway_dir="${asset_root}/payload/media/gateway-bootstrap"
	mkdir -p "${gateway_dir}"
	for script in \
		gateway-bootstrap-linux.sh \
		gateway-install-lensnode-sidecar.sh \
		gateway-lifecycle.sh \
		gateway-install-docker-ubuntu-amd64.sh; do
		cp "${ROOT}/deploy/bootstrap/${script}" "${gateway_dir}/${script}"
		chmod 755 "${gateway_dir}/${script}"
	done
	cp "${ROOT}/deploy/installer/sourcelens/hfl-sentry-sitecustomize.py" \
		"${gateway_dir}/hfl-sentry-sitecustomize.py"
	metadata="$(find "${input_dir}" -type f -name 'sourcelens-lensnode.json' -print -quit)"
	[[ -s "${metadata}" ]]
	ref="$(jq -r '.sources[] | select(.region == "global") | .ref' "${metadata}")"
	digest="$(jq -r '.digest' "${metadata}")"
	immutable_ref="${ref%:*}@${digest}"
	docker pull --platform linux/amd64 "${immutable_ref}"
	docker tag "${immutable_ref}" hyperfilelens-sourcelens-lensnode:latest
	docker tag "${immutable_ref}" sourcelens-lensnode:latest
	partial="${gateway_dir}/lensnode-image-linux-amd64.tar.gz.part"
	# The Gateway bundle uses stable local aliases. Omitting the HFL-versioned
	# runtime tag keeps this large layer reusable while the SourceLens image is unchanged.
	docker save hyperfilelens-sourcelens-lensnode:latest sourcelens-lensnode:latest \
		| gzip -1 -n >"${partial}"
	mv "${partial}" "${gateway_dir}/lensnode-image-linux-amd64.tar.gz"
	;;
language)
	extract_matching '_internal-language-packs.tar'
	compgen -G "${asset_root}/payload/language-packs/hyperfilelens-lang-*-${version}.tar.gz" >/dev/null
	;;
esac

if find "${asset_root}" -type l -print -quit | grep -q .; then
	printf 'ERROR: SaaS asset context contains symbolic links\n' >&2
	exit 1
fi
printf '%s\n' "${kind}" >"${asset_root}/.asset-kind"
case "${kind}" in
agent)
	cat >"${output_dir}/Dockerfile" <<'DOCKERFILE'
FROM scratch
COPY rootfs/opt/hyperfilelens-assets/payload/media/agent-releases /opt/hyperfilelens-assets/payload/media/agent-releases
COPY rootfs/opt/hyperfilelens-assets/payload/media/enroll-bootstrap /opt/hyperfilelens-assets/payload/media/enroll-bootstrap
COPY rootfs/opt/hyperfilelens-assets/.asset-kind /opt/hyperfilelens-assets/.asset-kind
DOCKERFILE
	;;
gateway)
	cat >"${output_dir}/Dockerfile" <<'DOCKERFILE'
FROM scratch
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/docker-debs-ubuntu2004-amd64.tar.gz /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/docker-debs-ubuntu2004-amd64.tar.gz
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/docker-debs-ubuntu2204-amd64.tar.gz /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/docker-debs-ubuntu2204-amd64.tar.gz
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/docker-debs-ubuntu2404-amd64.tar.gz /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/docker-debs-ubuntu2404-amd64.tar.gz
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/lensnode-image-linux-amd64.tar.gz /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/lensnode-image-linux-amd64.tar.gz
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-bootstrap-linux.sh /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-bootstrap-linux.sh
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-install-lensnode-sidecar.sh /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-install-lensnode-sidecar.sh
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-lifecycle.sh /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-lifecycle.sh
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-install-docker-ubuntu-amd64.sh /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/gateway-install-docker-ubuntu-amd64.sh
COPY rootfs/opt/hyperfilelens-assets/payload/media/gateway-bootstrap/hfl-sentry-sitecustomize.py /opt/hyperfilelens-assets/payload/media/gateway-bootstrap/hfl-sentry-sitecustomize.py
COPY rootfs/opt/hyperfilelens-assets/.asset-kind /opt/hyperfilelens-assets/.asset-kind
DOCKERFILE
	;;
language)
	cat >"${output_dir}/Dockerfile" <<'DOCKERFILE'
FROM scratch
COPY rootfs/opt/hyperfilelens-assets/payload/language-packs /opt/hyperfilelens-assets/payload/language-packs
COPY rootfs/opt/hyperfilelens-assets/.asset-kind /opt/hyperfilelens-assets/.asset-kind
DOCKERFILE
	;;
esac
