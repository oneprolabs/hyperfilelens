#!/usr/bin/env bash
# Deploy one already-built Enterprise SaaS candidate from registry images.
set -euo pipefail

candidate_archive=""
candidate_sha256=""
registry_credentials=""
runtime_env_file=""
direct_host=""
public_url=""
admin_public_url=""

while [[ $# -gt 0 ]]; do
	case "$1" in
	--candidate) candidate_archive=${2:-}; shift 2 ;;
	--candidate-sha256) candidate_sha256=${2:-}; shift 2 ;;
	--registry-credentials) registry_credentials=${2:-}; shift 2 ;;
	--runtime-env-file) runtime_env_file=${2:-}; shift 2 ;;
	--direct-host) direct_host=${2:-}; shift 2 ;;
	--public-url) public_url=${2:-}; shift 2 ;;
	--admin-public-url) admin_public_url=${2:-}; shift 2 ;;
	*) printf 'ERROR: unknown argument: %s\n' "$1" >&2; exit 2 ;;
	esac
done

[[ "${candidate_archive}" =~ ^/var/tmp/hyperfilelens-saas-[0-9]+-[0-9]+/candidate\.tar\.gz$ ]]
[[ "${registry_credentials}" =~ ^/var/tmp/hyperfilelens-saas-[0-9]+-[0-9]+/registry\.json$ ]]
[[ "${runtime_env_file}" =~ ^/var/tmp/hyperfilelens-saas-[0-9]+-[0-9]+/runtime\.env$ ]]
[[ "${candidate_sha256}" =~ ^[0-9a-f]{64}$ ]]
[[ -n "${direct_host}" && "${direct_host}" != *[[:space:]]* ]]
for file in "${candidate_archive}" "${registry_credentials}" "${runtime_env_file}"; do
	[[ -f "${file}" && ! -L "${file}" ]] || {
		printf 'ERROR: staged SaaS deployment file is missing or unsafe: %s\n' "${file}" >&2
		exit 1
	}
	directory="$(dirname "${file}")"
	[[ "$(stat -c '%a' "${directory}")" == "700" ]] || {
		printf 'ERROR: SaaS staging directory must use mode 0700\n' >&2
		exit 1
	}
done
printf '%s  %s\n' "${candidate_sha256}" "${candidate_archive}" | sha256sum -c -

command -v docker >/dev/null
docker info >/dev/null
command -v python3 >/dev/null
command -v flock >/dev/null

exec 9>/var/lock/hyperfilelens-deploy.lock
flock 9

stage_dir="$(dirname "${candidate_archive}")"
extract_dir="${stage_dir}/extract"
docker_config="${stage_dir}/docker-config"
asset_container=""
rm -rf -- "${extract_dir}" "${docker_config}"
install -d -m 0700 "${extract_dir}" "${docker_config}"
cleanup() {
	rc=$?
	if [[ -n "${asset_container}" ]]; then
		docker rm -f "${asset_container}" >/dev/null 2>&1 || true
	fi
	rm -rf -- \
		"${docker_config}" "${extract_dir}" \
		"${stage_dir}/asset-agent" \
		"${stage_dir}/asset-gateway" \
		"${stage_dir}/asset-language"
	rm -f -- \
		"${stage_dir}/assets.tsv" \
		"${registry_credentials}" \
		"${runtime_env_file}" \
		"${candidate_archive}"
	exit "${rc}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

python3 - "${candidate_archive}" <<'PY'
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
with tarfile.open(archive, "r:gz") as package:
    members = package.getmembers()
    roots = set()
    for member in members:
        raw = member.name
        path = pathlib.PurePosixPath(raw)
        if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise SystemExit(f"candidate contains an unsafe path: {raw!r}")
        roots.add(path.parts[0])
        if not (member.isdir() or member.isreg()):
            raise SystemExit(f"candidate contains an unsupported entry: {raw!r}")
    if len(roots) != 1:
        raise SystemExit("candidate must contain exactly one package root")
PY
tar -xzf "${candidate_archive}" -C "${extract_dir}"
mapfile -t roots < <(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d -print)
[[ ${#roots[@]} -eq 1 && -f "${roots[0]}/MANIFEST.json" ]] || {
	printf 'ERROR: SaaS candidate has an invalid package layout\n' >&2
	exit 1
}
candidate_root=${roots[0]}

read_credential() {
	python3 - "${registry_credentials}" "$1" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get(sys.argv[2], "")
if not isinstance(value, str) or "\n" in value or "\r" in value or "\0" in value:
    raise SystemExit("invalid registry credential")
print(value, end="")
PY
}

registry_login_count=0
for prefix in global cn; do
	host="$(read_credential "${prefix}_host")"
	username="$(read_credential "${prefix}_username")"
	password="$(read_credential "${prefix}_password")"
	[[ "${host}" =~ ^[a-z0-9][a-z0-9.-]*(:[0-9]+)?$ && -n "${username}" && -n "${password}" ]]
	if printf '%s' "${password}" | DOCKER_CONFIG="${docker_config}" docker login \
		--username "${username}" --password-stdin "${host}" >/dev/null 2>&1; then
		registry_login_count=$((registry_login_count + 1))
		printf '[ OK ] Authenticated registry source: %s\n' "${host}"
	else
		printf '[WARN] Registry source is temporarily unavailable: %s\n' "${host}" >&2
	fi
	unset password
done
((registry_login_count > 0)) || {
	printf 'ERROR: neither registry source is available\n' >&2
	exit 1
}

export DOCKER_CONFIG="${docker_config}"
python3 - "${candidate_root}/MANIFEST.json" >"${stage_dir}/assets.tsv" <<'PY'
import json, pathlib, re, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
delivery = manifest.get("delivery") or {}
if delivery.get("mode") != "registry":
    raise SystemExit("candidate is not registry-backed")
asset_images = delivery.get("asset_images") or []
if len(asset_images) != 3:
    raise SystemExit("candidate must contain exactly three asset images")
seen = set()
for image in asset_images:
    kind = str(image.get("asset_kind") or "")
    digest = str(image.get("digest") or "")
    local_ref = str(image.get("local_ref") or "")
    sources = image.get("sources") or []
    if kind not in {"agent", "gateway", "language"} or kind in seen:
        raise SystemExit("candidate contains invalid or duplicate asset metadata")
    seen.add(kind)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit("candidate contains invalid asset metadata")
    if not re.fullmatch(
        rf"hyperfilelens-{kind}-assets:[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}",
        local_ref,
    ):
        raise SystemExit("candidate contains invalid asset local reference")
    refs = [str(item.get("ref") or "") for item in sources]
    if len(refs) != 2 or any(
        not re.fullmatch(
            r"[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/[a-z0-9._/-]+:[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            ref,
        )
        for ref in refs
    ):
        raise SystemExit("candidate contains invalid asset sources")
    print("\t".join([kind, digest, local_ref, *refs]))
PY

while IFS=$'\t' read -r kind digest local_ref source_one source_two; do
	[[ -n "${kind}" ]] || continue
	pulled=""
	for source_ref in "${source_one}" "${source_two}"; do
		[[ -n "${source_ref}" ]] || continue
		immutable_ref="${source_ref%:*}@${digest}"
		if docker pull --platform linux/amd64 "${immutable_ref}"; then
			pulled="${immutable_ref}"
			break
		fi
	done
	[[ -n "${pulled}" ]] || {
		printf 'ERROR: could not pull %s asset image\n' "${kind}" >&2
		exit 1
	}
	docker tag "${pulled}" "${local_ref}"
	asset_extract="${stage_dir}/asset-${kind}"
	rm -rf -- "${asset_extract}"
	install -d -m 0700 "${asset_extract}"
	asset_container="$(docker create "${local_ref}" /bin/true)"
	if ! docker cp "${asset_container}:/opt/hyperfilelens-assets/." "${asset_extract}/"; then
		docker rm -f "${asset_container}" >/dev/null 2>&1 || true
		asset_container=""
		exit 1
	fi
	docker rm -f "${asset_container}" >/dev/null
	asset_container=""
	[[ "$(cat "${asset_extract}/.asset-kind" 2>/dev/null || true)" == "${kind}" ]] || {
		printf 'ERROR: %s asset image has an invalid payload marker\n' "${kind}" >&2
		exit 1
	}
	python3 - "${asset_extract}" "${kind}" <<'PY'
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
kind = sys.argv[2]
prefixes = {
    "agent": (
        pathlib.PurePosixPath("payload/media/agent-releases"),
        pathlib.PurePosixPath("payload/media/enroll-bootstrap"),
    ),
    "gateway": (pathlib.PurePosixPath("payload/media/gateway-bootstrap"),),
    "language": (pathlib.PurePosixPath("payload/language-packs"),),
}[kind]
ancestors = {pathlib.PurePosixPath(".asset-kind")}
for prefix in prefixes:
    ancestors.update(prefix.parents[:-1])
    ancestors.add(prefix)

seen_prefixes = set()


def is_under(path: pathlib.PurePosixPath, prefix: pathlib.PurePosixPath) -> bool:
    try:
        path.relative_to(prefix)
    except ValueError:
        return False
    return True


for current, directories, files in os.walk(root, topdown=True, followlinks=False):
    for name in [*directories, *files]:
        path = pathlib.Path(current, name)
        relative = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        mode = path.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise SystemExit(f"asset {kind} contains unsupported entry: {relative}")
        allowed = relative in ancestors or any(is_under(relative, prefix) for prefix in prefixes)
        if not allowed:
            raise SystemExit(f"asset {kind} contains unexpected path: {relative}")
        for prefix in prefixes:
            if is_under(relative, prefix):
                seen_prefixes.add(prefix)
if seen_prefixes != set(prefixes):
    missing = sorted(str(prefix) for prefix in set(prefixes) - seen_prefixes)
    raise SystemExit(f"asset {kind} is missing payload roots: {missing}")
PY
	find "${asset_extract}" -type d -exec chmod 0755 {} +
	find "${asset_extract}" -type f -exec chmod 0644 {} +
	find "${asset_extract}/payload" -type f -name '*.sh' -exec chmod 0755 {} +
	cp -a "${asset_extract}/payload/." "${candidate_root}/payload/"
done <"${stage_dir}/assets.tsv"

if find "${candidate_root}/payload" -type l -print -quit | grep -q .; then
	printf 'ERROR: extracted SaaS assets contain symbolic links\n' >&2
	exit 1
fi

install -d -m 0700 /opt/hyperfilelens/data/deployment-candidates
previous_manifest="/opt/hyperfilelens/MANIFEST.json"
if [[ -f "${previous_manifest}" ]]; then
	previous_id="$(sha256sum "${previous_manifest}" | cut -c1-12)"
	install -d -m 0700 "/opt/hyperfilelens/data/deployment-candidates/${previous_id}"
	cp "${previous_manifest}" "/opt/hyperfilelens/data/deployment-candidates/${previous_id}/MANIFEST.json"
fi

upgrade_args=(
	upgrade --from "${candidate_root}" --yes --with-sourcelens
	--direct-host "${direct_host}"
	--runtime-env-file "${runtime_env_file}"
)
[[ -z "${public_url}" ]] || upgrade_args+=(--public-url "${public_url}")
[[ -z "${admin_public_url}" ]] || upgrade_args+=(--admin-public-url "${admin_public_url}")
HFL_UPGRADE_ARTIFACT_SHA256="${candidate_sha256}" \
	bash "${candidate_root}/install.sh" "${upgrade_args[@]}"

candidate_id="$(sha256sum "${candidate_root}/MANIFEST.json" | cut -c1-12)"
candidate_history="/opt/hyperfilelens/data/deployment-candidates/${candidate_id}"
install -d -m 0700 "${candidate_history}"
cp "${candidate_root}/MANIFEST.json" "${candidate_history}/MANIFEST.json"
printf '%s\n' "${candidate_id}" >"/opt/hyperfilelens/data/deployment-candidates/current"
if [[ -n "${previous_id:-}" && "${previous_id}" != "${candidate_id}" ]]; then
	printf '%s\n' "${previous_id}" >"/opt/hyperfilelens/data/deployment-candidates/previous"
fi
