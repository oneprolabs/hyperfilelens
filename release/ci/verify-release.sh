#!/usr/bin/env bash
# Validate a final release asset and optionally perform a full ephemeral install.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

install=0
archive=""
while [[ $# -gt 0 ]]; do
	case "$1" in
	--archive) archive=${2:-}; shift 2 ;;
	--install) install=1; shift ;;
	*) printf 'ERROR: unknown argument: %s\n' "$1" >&2; exit 2 ;;
	esac
done
[[ -s "${archive}" ]] || { printf 'ERROR: release archive is missing\n' >&2; exit 2; }
gzip -t "${archive}"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
tar -xzf "${archive}" -C "${tmp}"
pkg_root="$(find "${tmp}" -mindepth 1 -maxdepth 1 -type d -name 'hyperfilelens-*' -print -quit)"
[[ -n "${pkg_root}" && -s "${pkg_root}/MANIFEST.json" ]] || {
	printf 'ERROR: invalid release package layout\n' >&2
	exit 1
}

python3 - "${pkg_root}" <<'PY'
import hashlib
import json
import pathlib
import re
import sys
import tarfile
import zipfile

root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
release_id = (root / "VERSION").read_text(encoding="utf-8").strip()
for image in manifest.get("images", []):
    path = root / image["file"]
    if not path.is_file():
        raise SystemExit(f"missing image archive: {image['file']}")
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    digest = checksum.hexdigest()
    if digest != image.get("sha256"):
        raise SystemExit(f"image checksum mismatch: {image['file']}")
tls_artifacts = (manifest.get("artifacts") or {}).get("default_tls") or {}
for artifact in tls_artifacts.values():
    path = root / artifact["file"]
    if not path.is_file():
        raise SystemExit(f"missing default TLS artifact: {artifact['file']}")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != artifact.get("sha256"):
        raise SystemExit(f"default TLS checksum mismatch: {artifact['file']}")
if len(tls_artifacts) != 3:
    raise SystemExit("release manifest must describe three default TLS artifacts")

language_packs = manifest.get("language_packs")
if not isinstance(language_packs, list) or not language_packs:
    raise SystemExit("release manifest must describe bundled language packs")
seen_pack_ids: set[str] = set()
for language_pack in language_packs:
    if not isinstance(language_pack, dict):
        raise SystemExit("release manifest has an invalid language-pack entry")
    pack_id = language_pack.get("id")
    if not isinstance(pack_id, str) or re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", pack_id
    ) is None:
        raise SystemExit("release manifest has an invalid language-pack id")
    if pack_id in seen_pack_ids:
        raise SystemExit(f"release manifest has duplicate language-pack id: {pack_id}")
    seen_pack_ids.add(pack_id)
    expected_file = (
        f"payload/language-packs/hyperfilelens-lang-{pack_id}-{release_id}.tar.gz"
    )
    if language_pack.get("file") != expected_file:
        raise SystemExit(f"invalid bundled language-pack path: {language_pack.get('file')}")
    archive_path = root / expected_file
    if not archive_path.is_file():
        raise SystemExit(f"missing bundled language pack: {expected_file}")
    if archive_path.stat().st_size != language_pack.get("size"):
        raise SystemExit(f"bundled language-pack size mismatch: {expected_file}")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != language_pack.get("sha256"):
        raise SystemExit(f"bundled language-pack checksum mismatch: {expected_file}")

    with tarfile.open(archive_path, "r:gz") as archive:
        members: dict[str, tarfile.TarInfo] = {}
        for member in archive.getmembers():
            normalized = member.name.removeprefix("./")
            member_path = pathlib.PurePosixPath(normalized)
            if not normalized or member_path.is_absolute() or ".." in member_path.parts:
                raise SystemExit(f"bundled language pack has an unsafe path: {expected_file}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise SystemExit(
                    f"bundled language pack has an unsupported entry: {expected_file}"
                )
            if member.isfile():
                if normalized in members:
                    raise SystemExit(
                        f"bundled language pack has a duplicate file: {expected_file}"
                    )
                members[normalized] = member
        required_files = {
            "manifest.json",
            "frontend/messages.json",
            "frontend/element-plus.json",
        }
        if not required_files.issubset(members):
            raise SystemExit(f"bundled language pack is incomplete: {expected_file}")
        backend_catalogs = [
            name
            for name in members
            if re.fullmatch(r"backend/locale/[^/]+/LC_MESSAGES/django[.]mo", name)
        ]
        if len(backend_catalogs) != 1:
            raise SystemExit(
                f"bundled language pack must contain one backend catalog: {expected_file}"
            )
        manifest_stream = archive.extractfile(members["manifest.json"])
        if manifest_stream is None:
            raise SystemExit(f"cannot read bundled language-pack manifest: {expected_file}")
        pack_manifest = json.load(manifest_stream)
    if (
        pack_manifest.get("schema") != 2
        or pack_manifest.get("id") != pack_id
        or pack_manifest.get("display_name") != language_pack.get("display_name")
        or pack_manifest.get("version") != release_id
        or pack_manifest.get("compatible_app") != f"=={release_id}"
    ):
        raise SystemExit(f"bundled language-pack identity mismatch: {expected_file}")
required_pack_ids = {"zh-hans", "es"}
missing_pack_ids = sorted(required_pack_ids - seen_pack_ids)
if missing_pack_ids:
    raise SystemExit(
        "required bundled language pack(s) are missing: "
        + ", ".join(missing_pack_ids)
    )

installer_root = root / "payload" / "media" / "enroll-bootstrap"
installer_manifest_path = installer_root / "INSTALLER_MANIFEST.json"
if not installer_manifest_path.is_file():
    raise SystemExit("missing minimal installer manifest")
installer_manifest = json.loads(installer_manifest_path.read_text(encoding="utf-8"))
if installer_manifest.get("schema_version") != 1:
    raise SystemExit("unsupported minimal installer manifest schema")
expected_installers = {
    "linux-amd64",
    "linux-arm64",
    "darwin-amd64",
    "darwin-arm64",
    "windows-amd64",
}
max_installer_bytes = 3_670_016
installers = installer_manifest.get("artifacts") or {}
if set(installers) != expected_installers:
    raise SystemExit("minimal installer manifest does not contain the complete platform matrix")
for key, artifact in installers.items():
    extension = "zip" if key.startswith("windows-") else "tar.gz"
    expected_filename = f"{release_id}/hfl-installer-{key}.{extension}"
    if artifact.get("filename") != expected_filename:
        raise SystemExit(f"invalid minimal installer path: {artifact.get('filename')}")
    path = installer_root / artifact["filename"]
    if not path.is_file():
        raise SystemExit(f"missing minimal installer archive: {artifact['filename']}")
    if path.stat().st_size != artifact.get("size"):
        raise SystemExit(f"minimal installer size mismatch: {artifact['filename']}")
    if path.stat().st_size > max_installer_bytes:
        raise SystemExit(
            f"minimal installer exceeds 3.5 MiB: {artifact['filename']} "
            f"({path.stat().st_size} bytes)"
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.get("sha256"):
        raise SystemExit(f"minimal installer checksum mismatch: {artifact['filename']}")
    if key.startswith("windows-"):
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != ["hfl-enroll.exe"]:
                raise SystemExit(f"invalid Windows minimal installer layout: {artifact['filename']}")
    else:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name != "hfl-enroll":
                raise SystemExit(f"invalid POSIX minimal installer layout: {artifact['filename']}")
            if not members[0].isfile():
                raise SystemExit(f"minimal installer is not a regular file: {artifact['filename']}")
            if members[0].mode & 0o111 == 0:
                raise SystemExit(f"minimal installer is not executable: {artifact['filename']}")
print(
    f"verified {len(manifest.get('images', []))} image archives "
    f"and {len(tls_artifacts)} default TLS artifacts; "
    f"verified {len(language_packs)} language packs; "
    f"verified {len(installers)} minimal installers"
)
PY

(
	cd "${pkg_root}/deploy/nginx/certs"
	sha256sum --strict --check SHA256SUMS
)
openssl verify -CAfile "${pkg_root}/deploy/nginx/certs/root-ca.crt" \
	"${pkg_root}/deploy/nginx/certs/tls.crt" >/dev/null
[[ -f "${pkg_root}/deploy/nginx/snippets/check-language-packs.sh" ]] || {
	printf 'ERROR: release package is missing the language-pack health check\n' >&2
	exit 1
}

artifact_channel="$(python3 - "${pkg_root}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(str(manifest.get("channel") or "release"))
PY
)"
[[ "${artifact_channel}" == "release" || "${artifact_channel}" == "main" ]] \
	|| { printf 'ERROR: unsupported artifact channel\n' >&2; exit 1; }

release_edition="$(python3 - "${pkg_root}/MANIFEST.json" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(str(manifest.get("edition") or ""))
PY
)"
[[ "${release_edition}" == "community" || "${release_edition}" == "enterprise" ]] \
	|| { printf 'ERROR: release manifest has an unsupported edition\n' >&2; exit 1; }

while IFS= read -r image_archive; do
	gzip -t "${image_archive}"
done < <(find "${pkg_root}/images" -maxdepth 1 -type f -name '*.tar.gz' | sort)
"${ROOT}/tools/quality/check-release-contracts.sh"

if [[ "${install}" -ne 1 ]]; then
	printf 'Release structure verification passed: %s\n' "${archive}"
	exit 0
fi

available_kib="$(df -Pk / | awk 'NR == 2 {print $4}')"
((available_kib >= 8 * 1024 * 1024)) || {
	printf 'ERROR: full install verification requires at least 8 GiB free\n' >&2
	exit 1
}

smoke_host="${SMOKE_HOST:-host.docker.internal}"
wait_for_release_services() {
	local deadline=$((SECONDS + 600))
	while ((SECONDS < deadline)); do
		if curl -kfsS https://127.0.0.1:11443/health/ready >/dev/null \
			&& curl -kfsS https://127.0.0.1:11444/ >/dev/null \
			&& curl -kfsS https://127.0.0.1:11445/ >/dev/null; then
			return 0
		fi
		sleep 5
	done
	docker compose -f /opt/hyperfilelens/docker-compose.yml --env-file /opt/hyperfilelens/.env ps || true
	printf 'ERROR: release services did not become ready\n' >&2
	return 1
}

verify_installed_language_packs() {
	local installed_version language_root
	installed_version="$(sudo sed -n 's/^HFL_PRODUCT_VERSION=//p' /opt/hyperfilelens/.env | head -1)"
	[[ -n "${installed_version}" ]] || {
		printf 'ERROR: installed product version is missing\n' >&2
		return 1
	}
	language_root="/opt/hyperfilelens/data/lang-packs/versions/${installed_version}"
	sudo python3 - "${language_root}/installed.json" "${installed_version}" <<'PY'
import json
import pathlib
import sys

index_path = pathlib.Path(sys.argv[1])
version = sys.argv[2]
index = json.loads(index_path.read_text(encoding="utf-8"))
if index.get("app_version") != version:
    raise SystemExit("installed language-pack index does not match the product version")
packs = index.get("packs")
if not isinstance(packs, list):
    raise SystemExit("installed language-pack index has no pack list")
required_pack_ids = {"zh-hans", "es"}
for pack_id in sorted(required_pack_ids):
    matching = [
        pack for pack in packs
        if isinstance(pack, dict) and pack.get("id") == pack_id
    ]
    if len(matching) != 1 or matching[0].get("version") != version:
        raise SystemExit(
            f"required language pack {pack_id} is not installed for this product version"
        )
PY
	sudo bash /opt/hyperfilelens/install.sh lang-pack list \
		| grep -Eq '^zh-hans[[:space:]]'
	sudo bash /opt/hyperfilelens/install.sh lang-pack list \
		| grep -Eq '^es[[:space:]]'
	for catalog_path in \
		/locales/installed.json \
		/locales/zh-hans/frontend/messages.json \
		/locales/zh-hans/frontend/element-plus.json \
		/locales/es/frontend/messages.json \
		/locales/es/frontend/element-plus.json; do
		curl -kfsS "https://127.0.0.1:11443${catalog_path}" \
			| python3 -c 'import json, sys; value = json.load(sys.stdin); assert isinstance(value, dict)'
	done
	printf 'Installed bundled language packs (zh-hans, es) verified for %s\n' "${installed_version}"
}

install_args=(install)
[[ "${artifact_channel}" == "main" ]] && install_args+=(--allow-main-build)
sudo env HFL_PUBLIC_HOST="${smoke_host}" HFL_SHOW_GENERATED_CREDENTIALS=0 \
	bash "${pkg_root}/install.sh" "${install_args[@]}"
wait_for_release_services
verify_installed_language_packs

printf 'Running same-version in-place upgrade verification\n'
upgrade_args=(upgrade --from "${pkg_root}" --yes)
[[ "${artifact_channel}" == "main" ]] && upgrade_args+=(--allow-main-build)
sudo env HFL_PUBLIC_HOST="${smoke_host}" HFL_SHOW_GENERATED_CREDENTIALS=0 \
	bash "${pkg_root}/install.sh" "${upgrade_args[@]}"
wait_for_release_services
verify_installed_language_packs

export HFL_WEBSITE_PORT=11442
export HFL_TENANT_PORT=11443
export HFL_ADMIN_PORT=11444
export SOURCELENS_CONSOLE_PORT=11445
export SMOKE_HOST="${smoke_host}"
export HFL_RELEASE_EDITION="${release_edition}"
export SEED_ADMIN_EMAIL
export SEED_ADMIN_PASSWORD
SEED_ADMIN_EMAIL="$(sudo sed -n 's/^SEED_ADMIN_EMAIL=//p' /opt/hyperfilelens/.env | head -1)"
SEED_ADMIN_PASSWORD="$(sudo sed -n 's/^SEED_ADMIN_PASSWORD=//p' /opt/hyperfilelens/.env | head -1)"
export SMOKE_REQUIRE_HMR=0
export SMOKE_SOURCELENS_ENV_FILE=/opt/hyperfilelens/data/sourcelens/config/.env
sudo env \
	HFL_WEBSITE_PORT="${HFL_WEBSITE_PORT}" \
	HFL_TENANT_PORT="${HFL_TENANT_PORT}" \
	HFL_ADMIN_PORT="${HFL_ADMIN_PORT}" \
	SOURCELENS_CONSOLE_PORT="${SOURCELENS_CONSOLE_PORT}" \
	SMOKE_HOST="${SMOKE_HOST}" \
	HFL_RELEASE_EDITION="${HFL_RELEASE_EDITION}" \
	SEED_ADMIN_EMAIL="${SEED_ADMIN_EMAIL}" \
	SEED_ADMIN_PASSWORD="${SEED_ADMIN_PASSWORD}" \
	SMOKE_REQUIRE_HMR="${SMOKE_REQUIRE_HMR}" \
	SMOKE_SOURCELENS_ENV_FILE="${SMOKE_SOURCELENS_ENV_FILE}" \
	"${ROOT}/tools/dev/browser-smoke.sh"
printf 'Full release install, upgrade, and login verification passed\n'
