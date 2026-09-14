#!/usr/bin/env bash
# Install or upgrade one already-built Enterprise SaaS candidate from registry images.
set -euo pipefail

resolve_deployment_action() {
	local install_root=$1 present=0 path
	local -a identity_files=(.env VERSION MANIFEST.json)
	if [[ -L "${install_root}" || ( -e "${install_root}" && ! -d "${install_root}" ) ]]; then
		printf 'ERROR: unsafe HyperFileLens installation root: %s\n' "${install_root}" >&2
		return 1
	fi
	for path in "${identity_files[@]}"; do
		if [[ -e "${install_root}/${path}" || -L "${install_root}/${path}" ]]; then
			present=$((present + 1))
		fi
	done
	if ((present == 0)); then
		if [[ -d "${install_root}" ]] \
			&& find "${install_root}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
			printf 'ERROR: HyperFileLens installation root contains unrecognized state: %s\n' \
				"${install_root}" >&2
			return 1
		fi
		printf 'install\n'
		return 0
	fi
	if ((present != ${#identity_files[@]})); then
		printf 'ERROR: incomplete HyperFileLens installation under %s; expected .env, VERSION, and MANIFEST.json together\n' \
			"${install_root}" >&2
		return 1
	fi
	for path in "${identity_files[@]}"; do
		if [[ ! -f "${install_root}/${path}" || -L "${install_root}/${path}" ]]; then
			printf 'ERROR: unsafe HyperFileLens installation identity file: %s\n' \
				"${install_root}/${path}" >&2
			return 1
		fi
	done
	printf 'upgrade\n'
}

candidate_archive=""
candidate_sha256=""
expected_tag=""
registry_credentials=""
registry_region=""
runtime_env_file=""
direct_host=""
public_url=""
admin_public_url=""

while [[ $# -gt 0 ]]; do
	case "$1" in
	--candidate) candidate_archive=${2:-}; shift 2 ;;
	--candidate-sha256) candidate_sha256=${2:-}; shift 2 ;;
	--expected-tag) expected_tag=${2:-}; shift 2 ;;
	--registry-credentials) registry_credentials=${2:-}; shift 2 ;;
	--registry-region) registry_region=${2:-}; shift 2 ;;
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
[[ "${expected_tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
[[ "${registry_region}" =~ ^(cn|global)$ ]]
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
install_root="/opt/hyperfilelens"
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
		"${stage_dir}/registry-pull.log" \
		"${stage_dir}/previous-MANIFEST.json" \
		"${registry_credentials}" \
		"${runtime_env_file}" \
		"${candidate_archive}"
	exit "${rc}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

deployment_action="$(resolve_deployment_action "${install_root}")"
printf '[INFO] Enterprise SaaS deployment action: %s\n' "${deployment_action}"
previous_id=""
previous_manifest_snapshot="${stage_dir}/previous-MANIFEST.json"
if [[ "${deployment_action}" == "upgrade" ]]; then
	previous_manifest="${install_root}/MANIFEST.json"
	previous_id="$(sha256sum "${previous_manifest}" | cut -c1-12)"
	cp "${previous_manifest}" "${previous_manifest_snapshot}"
fi

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
python3 - "${candidate_root}/MANIFEST.json" "${expected_tag}" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
expected_tag = sys.argv[2]
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    raise SystemExit(f"candidate manifest is invalid: {error}") from error
expected_version = expected_tag[1:]
if manifest.get("product") != "hyperfilelens":
    raise SystemExit("candidate product identity is invalid")
if manifest.get("edition") != "enterprise":
    raise SystemExit("candidate is not Enterprise edition")
if manifest.get("channel") != "release":
    raise SystemExit("candidate is not a release build")
if manifest.get("artifact_id") != expected_tag or manifest.get("version") != expected_version:
    raise SystemExit("candidate does not match the requested release tag")
if (manifest.get("delivery") or {}).get("mode") != "registry":
    raise SystemExit("candidate is not registry-backed")
PY

read_credential() {
	python3 - "${registry_credentials}" "$1" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get(sys.argv[2], "")
if not isinstance(value, str) or "\n" in value or "\r" in value or "\0" in value:
    raise SystemExit("invalid registry credential")
print(value, end="")
PY
}

registry_host="$(read_credential "${registry_region}_host")"
registry_username="$(read_credential "${registry_region}_username")"
registry_password="$(read_credential "${registry_region}_password")"
registry_name="Docker Hub"
[[ "${registry_region}" == "cn" ]] && registry_name="Alibaba Cloud"
[[ "${registry_host}" =~ ^[a-z0-9][a-z0-9.-]*(:[0-9]+)?$ \
	&& -n "${registry_username}" && -n "${registry_password}" ]]
if ! printf '%s' "${registry_password}" | DOCKER_CONFIG="${docker_config}" docker login \
	--username "${registry_username}" --password-stdin "${registry_host}" >/dev/null 2>&1; then
	printf 'ERROR: selected registry is unavailable: %s\n' "${registry_host}" >&2
	exit 1
fi
unset registry_password
printf '[ OK ] Authenticated registry source: %s\n' "${registry_host}"

export DOCKER_CONFIG="${docker_config}"
registry_pull_retry_delay_config="${HFL_REGISTRY_PULL_RETRY_DELAY_SECONDS:-15}"
if [[ ! "${registry_pull_retry_delay_config}" =~ ^[0-9]+$ ]] \
	|| ((registry_pull_retry_delay_config > 300)); then
	printf 'ERROR: HFL_REGISTRY_PULL_RETRY_DELAY_SECONDS must be an integer from 0 to 300\n' >&2
	exit 2
fi
registry_pull_retry_delay=${registry_pull_retry_delay_config}
((registry_pull_retry_delay <= 60)) || registry_pull_retry_delay=60
registry_pull_attempts=5

registry_retry_delay_for_attempt() {
	local completed_attempt=$1 delay=${registry_pull_retry_delay} count=1
	while ((count < completed_attempt)); do
		delay=$((delay * 2))
		((delay < 60)) || {
			delay=60
			break
		}
		count=$((count + 1))
	done
	printf '%s' "${delay}"
}

registry_pull_is_transient() {
	grep -Eiq \
		'network is unreachable|no route to host|connection (refused|reset|timed out)|i/o timeout|context deadline exceeded|tls handshake timeout|client\.timeout|request canceled|unexpected eof|short read|unexpected commit digest|too many requests|(http (response )?status|status( code)?( from [^:]+)?)[^0-9]{0,16}(429|5[0-9][0-9])' \
		"$1"
}

pull_registry_image() {
	local immutable_ref=$1 attempt=1 rc=1 delay
	local diagnostics="${stage_dir}/registry-pull.log"
	while ((attempt <= registry_pull_attempts)); do
		: >"${diagnostics}"
		if docker pull --platform linux/amd64 "${immutable_ref}" 2>&1 \
			| tee "${diagnostics}"; then
			rm -f -- "${diagnostics}"
			return 0
		else
			rc=$?
		fi
		if ((attempt < registry_pull_attempts)) \
			&& registry_pull_is_transient "${diagnostics}"; then
			delay="$(registry_retry_delay_for_attempt "${attempt}")"
			printf '[WARN] Temporary %s image download error; retrying in %s seconds (%s/%s)\n' \
				"${registry_name}" "${delay}" "$((attempt + 1))" \
				"${registry_pull_attempts}" >&2
			sleep "${delay}"
			attempt=$((attempt + 1))
			continue
		fi
		break
	done
	rm -f -- "${diagnostics}"
	return "${rc}"
}

python3 - "${candidate_root}/MANIFEST.json" "${registry_region}" >"${stage_dir}/assets.tsv" <<'PY'
import json, pathlib, re, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
selected_region = sys.argv[2]
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
    sources_by_region = {str(item.get("region") or ""): item for item in sources}
    if len(sources) != 2 or set(sources_by_region) != {"cn", "global"}:
        raise SystemExit("candidate asset sources must contain cn and global regions")
    refs = {
        region: str(sources_by_region[region].get("ref") or "")
        for region in ("cn", "global")
    }
    if any(
        not re.fullmatch(
            r"[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/[a-z0-9._/-]+:[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            ref,
        )
        for ref in refs.values()
    ):
        raise SystemExit("candidate contains invalid asset sources")
    print("\t".join([kind, digest, local_ref, refs[selected_region]]))
PY

while IFS=$'\t' read -r kind digest local_ref source_ref; do
	[[ -n "${kind}" ]] || continue
	immutable_ref="${source_ref%:*}@${digest}"
	if ! pull_registry_image "${immutable_ref}"; then
		printf 'ERROR: could not pull %s asset image from selected %s registry (%s)\n' \
			"${kind}" "${registry_name}" "${registry_host}" >&2
		exit 1
	fi
	docker tag "${immutable_ref}" "${local_ref}"
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
    ancestors.update(list(prefix.parents)[:-1])
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

deployment_args=(
	"${deployment_action}" --yes --with-sourcelens
	--direct-host "${direct_host}"
	--runtime-env-file "${runtime_env_file}"
)
if [[ "${deployment_action}" == "upgrade" ]]; then
	deployment_args+=(--from "${candidate_root}")
fi
[[ -z "${public_url}" ]] || deployment_args+=(--public-url "${public_url}")
[[ -z "${admin_public_url}" ]] || deployment_args+=(--admin-public-url "${admin_public_url}")
if [[ "${deployment_action}" == "upgrade" ]]; then
	HFL_REGISTRY_REGION="${registry_region}" \
		HFL_UPGRADE_ARTIFACT_SHA256="${candidate_sha256}" \
		bash "${candidate_root}/install.sh" "${deployment_args[@]}"
else
	HFL_REGISTRY_REGION="${registry_region}" \
		bash "${candidate_root}/install.sh" "${deployment_args[@]}"
fi

candidate_id="$(sha256sum "${candidate_root}/MANIFEST.json" | cut -c1-12)"
candidate_history_root="${install_root}/data/deployment-candidates"
candidate_history="${candidate_history_root}/${candidate_id}"
install -d -m 0700 "${candidate_history_root}"
if [[ -n "${previous_id}" ]]; then
	previous_history="${candidate_history_root}/${previous_id}"
	install -d -m 0700 "${previous_history}"
	cp "${previous_manifest_snapshot}" "${previous_history}/MANIFEST.json"
fi
install -d -m 0700 "${candidate_history}"
cp "${candidate_root}/MANIFEST.json" "${candidate_history}/MANIFEST.json"
printf '%s\n' "${candidate_id}" >"${candidate_history_root}/current"
if [[ -n "${previous_id:-}" && "${previous_id}" != "${candidate_id}" ]]; then
	printf '%s\n' "${previous_id}" >"${candidate_history_root}/previous"
fi

# Candidate manifests are small, but an upgrade can run frequently. Keep the
# current, previous, and three most recent entries; remove only validated
# twelve-character manifest directories so unrelated data is never touched.
if ! python3 - <<'PY'
import pathlib
import re
import shutil

root = pathlib.Path("/opt/hyperfilelens/data/deployment-candidates")
if root.is_dir():
    pointers = {
        (root / name).read_text(encoding="utf-8").strip()
        for name in ("current", "previous")
        if (root / name).is_file()
    }
    entries = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and re.fullmatch(r"[0-9a-f]{12}", path.name)
            and (path / "MANIFEST.json").is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    keep = pointers | {path.name for path in entries[:3]}
    for path in entries:
        if path.name not in keep:
            shutil.rmtree(path)
PY
then
	# The release has already been applied and health-checked. Candidate
	# history is housekeeping; a transient permission or filesystem error must
	# not turn a successful deployment into a failed CI run.
	printf '[WARN] Unable to prune old deployment candidates; retaining them for the next run.\n' >&2
fi
