#!/usr/bin/env bash
# Validate and atomically retain one Enterprise release on the TEST host.
set -euo pipefail

tag=${1:-}
incoming=${2:-}
store_root=${3:-/root/hfl-release}
keep=${4:-10}

[[ "${tag}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || {
	printf 'ERROR: invalid Enterprise release tag\n' >&2
	exit 2
}
version=${BASH_REMATCH[1]}
[[ "${incoming}" == "${store_root}/.incoming/"* ]] || {
	printf 'ERROR: incoming directory must be below %s/.incoming\n' "${store_root}" >&2
	exit 2
}
incoming_name=${incoming#"${store_root}/.incoming/"}
[[ "${incoming_name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
	printf 'ERROR: unsafe incoming directory name\n' >&2
	exit 2
}
[[ "${keep}" =~ ^[0-9]+$ ]] || { printf 'ERROR: invalid retention count\n' >&2; exit 2; }
[[ -d "${incoming}" && ! -L "${incoming}" ]] || { printf 'ERROR: upload is missing\n' >&2; exit 1; }

exec 9>"${store_root}/.lock"
flock 9

(
	cd "${incoming}"
	[[ -s SHA256SUMS && -s MANIFEST.json ]] || {
		printf 'ERROR: Enterprise release metadata is incomplete\n' >&2
		exit 1
	}
	sha256sum -c SHA256SUMS
	archive="hyperfilelens-${version}-ee.tar.gz"
	if [[ ! -f "${archive}" ]]; then
		first="${archive}.part-000"
		[[ -s "${first}" ]] || { printf 'ERROR: Enterprise archive is missing\n' >&2; exit 1; }
		cat "${archive}.part-"* >"${archive}.part"
		mv "${archive}.part" "${archive}"
	fi
	python3 - MANIFEST.json "${tag}" "${archive}" <<'PY'
import json
import pathlib
import re
import sys
import tarfile

manifest_path = pathlib.Path(sys.argv[1])
tag = sys.argv[2]
archive_path = pathlib.Path(sys.argv[3])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
version = tag[1:] if tag.startswith("v") else tag
if manifest.get("edition") != "enterprise":
    raise SystemExit("release is not Enterprise edition")
if manifest.get("version") != version or manifest.get("artifact_id") != tag:
    raise SystemExit("Enterprise release identity mismatch")
image_version = str(manifest.get("image_version") or "")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", image_version):
    raise SystemExit("Enterprise image identity is missing or invalid")
runtime_images = manifest.get("runtime_images") or {
    "backend": f"hyperfilelens-backend:{image_version}",
    "frontend": f"hyperfilelens-frontend:{image_version}",
}
if set(runtime_images) != {"backend", "frontend"}:
    raise SystemExit("Enterprise runtime image identity is incomplete")
hfl_refs = {
    str(ref)
    for item in manifest.get("images", [])
    if item.get("role") == "hyperfilelens"
    for ref in (item.get("refs") or [])
}
for role, ref in runtime_images.items():
    if not re.fullmatch(
        rf"hyperfilelens-{role}:[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}",
        str(ref),
    ) or ref not in hfl_refs:
        raise SystemExit(f"Enterprise {role} runtime image is not in the HFL archive")
extension_commit = manifest.get("extension_commit", "")
if not isinstance(extension_commit, str) or len(extension_commit) != 40 or any(
    character not in "0123456789abcdef" for character in extension_commit
):
    raise SystemExit("Enterprise extension identity is missing or invalid")
with tarfile.open(archive_path, "r:gz") as archive:
    members = [item for item in archive.getmembers() if item.name.endswith("/MANIFEST.json")]
    if len(members) != 1:
        raise SystemExit("Enterprise archive manifest is missing or ambiguous")
    stream = archive.extractfile(members[0])
    if stream is None or json.load(stream) != manifest:
        raise SystemExit("external and archived manifests differ")
PY
	# The canonical, reconstructed archive is the only package payload retained.
	find . -maxdepth 1 -type f -name '*.part-*' -delete
	find . -maxdepth 1 -type f \
		! -name "${archive}" ! -name MANIFEST.json ! -name SHA256SUMS -delete
	sha256sum "${archive}" MANIFEST.json >SHA256SUMS
)

destination="${store_root}/${tag}"
if [[ -e "${destination}" ]]; then
	[[ -d "${destination}" && ! -L "${destination}" ]] || {
		printf 'ERROR: immutable Enterprise destination is not a safe directory\n' >&2
		exit 1
	}
	(
		cd "${destination}"
		sha256sum -c SHA256SUMS
		python3 - MANIFEST.json "${tag}" "hyperfilelens-${version}-ee.tar.gz" <<'PY'
import json
import pathlib
import re
import sys
import tarfile

manifest_path = pathlib.Path(sys.argv[1])
tag = sys.argv[2]
archive_path = pathlib.Path(sys.argv[3])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
version = tag[1:]
if manifest.get("edition") != "enterprise":
    raise SystemExit("stored release is not Enterprise edition")
if manifest.get("version") != version or manifest.get("artifact_id") != tag:
    raise SystemExit("stored Enterprise release identity mismatch")
image_version = str(manifest.get("image_version") or "")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", image_version):
    raise SystemExit("stored Enterprise image identity is missing or invalid")
runtime_images = manifest.get("runtime_images") or {
    "backend": f"hyperfilelens-backend:{image_version}",
    "frontend": f"hyperfilelens-frontend:{image_version}",
}
if set(runtime_images) != {"backend", "frontend"}:
    raise SystemExit("stored Enterprise runtime image identity is incomplete")
hfl_refs = {
    str(ref)
    for item in manifest.get("images", [])
    if item.get("role") == "hyperfilelens"
    for ref in (item.get("refs") or [])
}
for role, ref in runtime_images.items():
    if not re.fullmatch(
        rf"hyperfilelens-{role}:[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}",
        str(ref),
    ) or ref not in hfl_refs:
        raise SystemExit(f"stored Enterprise {role} runtime image is not in the HFL archive")
extension_commit = manifest.get("extension_commit", "")
if not isinstance(extension_commit, str) or len(extension_commit) != 40 or any(
    character not in "0123456789abcdef" for character in extension_commit
):
    raise SystemExit("stored Enterprise extension identity is missing or invalid")
with tarfile.open(archive_path, "r:gz") as archive:
    members = [item for item in archive.getmembers() if item.name.endswith("/MANIFEST.json")]
    if len(members) != 1:
        raise SystemExit("stored Enterprise archive manifest is missing or ambiguous")
    stream = archive.extractfile(members[0])
    if stream is None or json.load(stream) != manifest:
        raise SystemExit("stored external and archived manifests differ")
PY
	)
	python3 - "${incoming}/MANIFEST.json" "${destination}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

incoming = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
stored = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
identity_fields = (
    "product",
    "edition",
    "version",
    "artifact_id",
    "image_version",
    "runtime_images",
    "git_commit",
    "extension_commit",
)
differences = [
    field for field in identity_fields if incoming.get(field) != stored.get(field)
]
if differences:
    raise SystemExit(
        "immutable Enterprise release identity differs for: " + ", ".join(differences)
    )
PY
	incoming_digest="$(sha256sum "${incoming}/hyperfilelens-${version}-ee.tar.gz" | awk '{print $1}')"
	stored_digest="$(sha256sum "${destination}/hyperfilelens-${version}-ee.tar.gz" | awk '{print $1}')"
	[[ "${incoming_digest}" == "${stored_digest}" ]] || {
		printf 'ERROR: immutable Enterprise package content differs for %s\n' "${tag}" >&2
		exit 1
	}
	printf 'Enterprise version %s already exists; retaining the immutable stored package\n' \
		"${tag}" >&2
	rm -rf -- "${incoming}"
else
	mv "${incoming}" "${destination}"
fi

if ((keep > 0)); then
	mapfile -t releases < <(
		find "${store_root}" -mindepth 1 -maxdepth 1 -type d -name 'v*' \
			-printf '%f\n' \
			| sed -n '/^v[0-9]\+\.[0-9]\+\.[0-9]\+$/p' \
			| sort -Vr \
			| sed "s#^#${store_root}/#"
	)
	for ((index = keep; index < ${#releases[@]}; index++)); do
		old=${releases[index]}
		[[ "${old}" =~ /v[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
		rm -rf -- "${old}"
	done
fi

[[ -d "${destination}" && ! -L "${destination}" \
	&& -f "${destination}/hyperfilelens-${version}-ee.tar.gz" ]] || {
	printf 'ERROR: Enterprise version %s is outside the retained SemVer window\n' \
		"${tag}" >&2
	exit 1
}
printf '%s\n' "${destination}/hyperfilelens-${version}-ee.tar.gz"
