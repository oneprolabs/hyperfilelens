#!/usr/bin/env bash
# Assemble customer-facing Agent archives from existing build and dependency inputs.
# This script does not compile source, download dependencies, or call other scripts.
set -euo pipefail
umask 022
export COPYFILE_DISABLE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${AGENT_ROOT}/../.." && pwd)"
# shellcheck source=../../../tools/lib/version.sh
source "${REPO_ROOT}/tools/lib/version.sh"
# shellcheck source=../../../tools/lib/logging.sh
source "${REPO_ROOT}/tools/lib/logging.sh"

DEFAULT_MATRIX="linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64"
BUNDLE="${AGENT_BUNDLE:-all}"
VERBOSE="${AGENT_VERBOSE:-0}"
LOG_FILE="${AGENT_LOG_FILE:-}"
PRINT_CONFIG=0
OPT_VERSION=""
OPT_MATRIX=""
OPT_COMMIT=""
OPT_BUNDLE=""
OPT_UBUNTU2404_ARCH=""
SESSION_STARTED=0

hfl_now() {
	date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ
}

hfl_finish_sentence() {
	local msg="$*"
	msg="${msg%"${msg##*[![:space:]]}"}"
	case "${msg}" in
	*. | *.? | *!) printf '%s' "${msg}" ;;
	*) printf '%s.' "${msg}" ;;
	esac
}

_hfl_emit_raw() {
	local level=$1 tag
	shift
	if [[ "${HFL_LOG_TERMINAL_TIMESTAMPS:-1}" == "1" ]]; then
		HFL_LOG_COMPONENT=agent \
			hfl_log_emit "${level// /}" "$(hfl_finish_sentence "$@")"
	else
		case "${level// /}" in
		INFO) tag='INFO ' ;;
		STEP) tag='....' ;;
		OK) tag=' OK ' ;;
		WARN) tag='WARN' ;;
		SKIP) tag='SKIP' ;;
		FAIL) tag='FAIL' ;;
		*) tag="${level}" ;;
		esac
		printf '[%s] %s\n' "${tag}" "$(hfl_finish_sentence "$@")" >&2
	fi
}

log_info() { _hfl_emit_raw "INFO " "$@"; }
log_ok() { _hfl_emit_raw " OK  " "$@"; }
log_step() { _hfl_emit_raw "STEP " "$@"; }
log_skip() { _hfl_emit_raw "SKIP " "$@"; }
log_warn() { _hfl_emit_raw "WARN " "$@"; }
log_fail() {
	local message=$1
	local code=${2:-1}
	_hfl_emit_raw "FAIL " "${message}"
	exit "${code}"
}

finish_session() {
	local rc=$?
	trap - EXIT
	if [[ "${SESSION_STARTED}" -eq 1 ]]; then
		if [[ "${rc}" -eq 0 ]]; then
			log_info "Agent packaging session finished successfully"
		else
			log_warn "Agent packaging session finished with errors (exit=${rc})"
		fi
	fi
	exit "${rc}"
}

require_value() {
	if [[ $# -lt 2 || -z "${2:-}" || "${2:0:1}" == "-" ]]; then
		printf 'ERROR: %s requires a value\n' "$1" >&2
		exit 2
	fi
}

usage() {
	cat <<'USAGE'
Usage: ./src/agent/scripts/package.sh [options]

Purpose:
  Assemble verified Agent archives from existing build outputs. This script
  does not compile source, fetch dependencies, or call another Agent script.

Bundle kinds (default: all):
  all                    Standard archives plus Linux Ubuntu 20.04/22.04/24.04 offline archives
  standard               Standard archives only
  ubuntu2004             Ubuntu 20.04 archives from existing standard archives
  ubuntu2204             Ubuntu 22.04 archives from existing standard archives
  ubuntu2404             Ubuntu 24.04 archives from existing standard archives

Inputs:
  build/agent/<version>/BUILD_INFO.json
  build/kopia/KOPIA_INFO.json
  build/kopia/dist/<os>/<arch>/kopia[.exe]
  build/agent/<version>/<os>/<arch>/hfl-agent-*
  build/agent/<version>/windows/<arch>/hfl-agent-user-launcher-*.exe
  build/dependencies/agent/ubuntu-{20.04,22.04,24.04}/<arch>/MANIFEST.json
  src/agent/packaging/

Outputs:
  build/agent/<version>/package/*.tar.gz
  build/agent/<version>/package/*.zip

Options:
  --version VERSION          Release version (env: RELEASE_VERSION)
  --matrix MATRIX            Space-separated os:arch list (env: AGENT_MATRIX)
  --commit COMMIT            Full build commit (env: AGENT_COMMIT)
  --bundle KIND              all | standard | ubuntu2004 | ubuntu2204 | ubuntu2404 (env: AGENT_BUNDLE)
  --ubuntu2404-arch ARCH     amd64 | arm64 | all for both Ubuntu bundles (compatibility name)
  --log-file FILE            Append console output to FILE (env: AGENT_LOG_FILE)
  --verbose                  Enable detailed configuration logging (env: AGENT_VERBOSE=1)
  --print-config             Print resolved configuration without packaging
  -h, --help                 Show this help

Supported matrix entries:
  linux:amd64 linux:arm64 darwin:amd64 darwin:arm64 windows:amd64

Precedence:
  CLI option > environment variable > exact Git tag/configured default

Exit codes:
  0 success; 1 packaging failure; 2 invalid input or missing tool;
  3 missing or mismatched prerequisite; 130 interrupted

Examples:
  ./src/agent/scripts/package.sh --bundle all
  ./src/agent/scripts/package.sh --bundle standard --matrix "linux:amd64 linux:arm64"
  ./src/agent/scripts/package.sh --bundle ubuntu2404 --matrix "linux:amd64" --ubuntu2404-arch amd64
USAGE
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--version) require_value "$1" "${2:-}"; OPT_VERSION=$2; shift 2 ;;
	--matrix) require_value "$1" "${2:-}"; OPT_MATRIX=$2; shift 2 ;;
	--commit) require_value "$1" "${2:-}"; OPT_COMMIT=$2; shift 2 ;;
	--bundle) require_value "$1" "${2:-}"; OPT_BUNDLE=$2; shift 2 ;;
	--ubuntu2404-arch) require_value "$1" "${2:-}"; OPT_UBUNTU2404_ARCH=$2; shift 2 ;;
	--log-file) require_value "$1" "${2:-}"; LOG_FILE=$2; shift 2 ;;
	--verbose) VERBOSE=1; shift ;;
	--print-config) PRINT_CONFIG=1; shift ;;
	-h | --help) usage; exit 0 ;;
	-*) printf 'ERROR: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
	*) printf 'ERROR: unexpected argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
	esac
done

case "${VERBOSE}" in
0 | 1 | true | false | yes | no) ;;
*) printf 'ERROR: AGENT_VERBOSE must be 0 or 1\n' >&2; exit 2 ;;
esac
case "${VERBOSE}" in true | yes) VERBOSE=1 ;; false | no) VERBOSE=0 ;; esac

[[ -n "${OPT_BUNDLE}" ]] && BUNDLE="${OPT_BUNDLE}"
AGENT_VERSION="$(normalize_artifact_id "${OPT_VERSION:-$(resolve_release_version)}")" || exit $?
MATRIX="${OPT_MATRIX:-${AGENT_MATRIX:-${DEFAULT_MATRIX}}}"
COMMIT="${OPT_COMMIT:-${AGENT_COMMIT:-$(resolve_commit_full "${REPO_ROOT}")}}"
UBUNTU2404_ARCH="${OPT_UBUNTU2404_ARCH:-${AGENT_UBUNTU2404_ARCH:-}}"
WORK_ROOT="${REPO_ROOT}/build/agent/${AGENT_VERSION}"
PACKAGE_DIR="${WORK_ROOT}/package"
NAS_DEPS_BASE="${REPO_ROOT}/build/dependencies/agent"
KOPIA_ROOT="${REPO_ROOT}/build/kopia"
INSTALL_DIR="${AGENT_ROOT}/packaging/install"
SYSTEMD_UNIT="${AGENT_ROOT}/packaging/systemd/hyperfilelens-agent.service"
GATEWAY_LIFECYCLE_SCRIPT="${REPO_ROOT}/deploy/bootstrap/gateway-lifecycle.sh"

case "${BUNDLE}" in
all) DO_STANDARD=1; UBUNTU_RELEASES="20.04 22.04 24.04" ;;
standard) DO_STANDARD=1; UBUNTU_RELEASES="" ;;
ubuntu2004) DO_STANDARD=0; UBUNTU_RELEASES="20.04" ;;
ubuntu2204) DO_STANDARD=0; UBUNTU_RELEASES="22.04" ;;
ubuntu2404) DO_STANDARD=0; UBUNTU_RELEASES="24.04" ;;
*) log_fail "Invalid bundle ${BUNDLE}; use all, standard, ubuntu2004, ubuntu2204, or ubuntu2404" 2 ;;
esac

validate_matrix() {
	local item seen=" "
	[[ -n "${MATRIX//[[:space:]]/}" ]] || log_fail "Matrix cannot be empty" 2
	for item in ${MATRIX}; do
		case "${item}" in
		linux:amd64 | linux:arm64 | darwin:amd64 | darwin:arm64 | windows:amd64) ;;
		*) log_fail "Unsupported matrix entry ${item}" 2 ;;
		esac
		if [[ "${seen}" == *" ${item} "* ]]; then
			log_fail "Duplicate matrix entry ${item}" 2
		fi
		seen+="${item} "
	done
}

validate_ubuntu_arch() {
	case "${UBUNTU2404_ARCH}" in
	"" | amd64 | arm64 | all) ;;
	*) log_fail "Invalid Ubuntu 24.04 architecture ${UBUNTU2404_ARCH}; use amd64, arm64, or all" 2 ;;
	esac
}

print_config() {
	cat <<CONFIG
bundle=${BUNDLE}
version=${AGENT_VERSION}
commit=${COMMIT}
matrix=${MATRIX}
ubuntu2404_arch=${UBUNTU2404_ARCH:-<from-matrix>}
build_input=${WORK_ROOT}
nas_input=${NAS_DEPS_BASE}/ubuntu-{20.04,22.04,24.04}
kopia_input=${KOPIA_ROOT}
output=${PACKAGE_DIR}
CONFIG
}

setup_log_file() {
	[[ "${HFL_PARENT_SESSION:-0}" != "1" ]] || return 0
	[[ "${HFL_LOG_TEE_ACTIVE:-0}" != "1" ]] || return 0
	[[ -n "${LOG_FILE}" ]] || return 0
	mkdir -p "$(dirname "${LOG_FILE}")"
	exec > >(tee -a "${LOG_FILE}") 2>&1
}

package_archives() {
	VERSION="${AGENT_VERSION}" MATRIX_VALUE="${MATRIX}" COMMIT_VALUE="${COMMIT}" \
		BUNDLE_VALUE="${BUNDLE}" UBUNTU_ARCH_VALUE="${UBUNTU2404_ARCH}" \
		WORK_ROOT_VALUE="${WORK_ROOT}" PACKAGE_DIR_VALUE="${PACKAGE_DIR}" \
		KOPIA_ROOT_VALUE="${KOPIA_ROOT}" \
		NAS_BASE_VALUE="${NAS_DEPS_BASE}" INSTALL_DIR_VALUE="${INSTALL_DIR}" \
		SYSTEMD_UNIT_VALUE="${SYSTEMD_UNIT}" \
		GATEWAY_LIFECYCLE_SCRIPT_VALUE="${GATEWAY_LIFECYCLE_SCRIPT}" \
		DO_STANDARD_VALUE="${DO_STANDARD}" \
		UBUNTU_RELEASES_VALUE="${UBUNTU_RELEASES}" python3 - <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

version = os.environ["VERSION"]
matrix = os.environ["MATRIX_VALUE"].split()
commit = os.environ["COMMIT_VALUE"]
bundle = os.environ["BUNDLE_VALUE"]
ubuntu_arch = os.environ["UBUNTU_ARCH_VALUE"]
work_root = Path(os.environ["WORK_ROOT_VALUE"])
kopia_root = Path(os.environ["KOPIA_ROOT_VALUE"])
package_dir = Path(os.environ["PACKAGE_DIR_VALUE"])
nas_base = Path(os.environ["NAS_BASE_VALUE"])
install_dir = Path(os.environ["INSTALL_DIR_VALUE"])
systemd_unit = Path(os.environ["SYSTEMD_UNIT_VALUE"])
gateway_lifecycle_script = Path(os.environ["GATEWAY_LIFECYCLE_SCRIPT_VALUE"])
do_standard = os.environ["DO_STANDARD_VALUE"] == "1"
ubuntu_releases = os.environ["UBUNTU_RELEASES_VALUE"].split()


class PrerequisiteError(RuntimeError):
    pass


def log(level: str, message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    message = message.rstrip()
    if message and message[-1] not in ".?!":
        message += "."
    if os.environ.get("HFL_LOG_TERMINAL_TIMESTAMPS", "1") != "1":
        tags = {
            "STEP ": "....",
            " OK  ": " OK ",
            "WARN ": "WARN",
            "SKIP ": "SKIP",
            "FAIL ": "FAIL",
        }
        print(f"[{tags.get(level, level.strip())}] {message}", file=sys.stderr, flush=True)
    else:
        print(f"[{timestamp}] [{level}] {message}", file=sys.stderr, flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise PrerequisiteError(f"missing {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrerequisiteError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PrerequisiteError(f"invalid {label}: expected a JSON object in {path}")
    return value


def validate_build_info() -> None:
    info = read_json(work_root / "BUILD_INFO.json", "build metadata")
    if info.get("version") != version:
        raise PrerequisiteError(
            f"BUILD_INFO.json version mismatch: expected {version}, got {info.get('version')!r}"
        )
    if info.get("commit") != commit:
        raise PrerequisiteError(
            f"BUILD_INFO.json commit mismatch: expected {commit}, got {info.get('commit')!r}"
        )
    if info.get("matrix") != matrix:
        raise PrerequisiteError(
            f"BUILD_INFO.json matrix mismatch: expected {matrix!r}, got {info.get('matrix')!r}"
        )


def validate_kopia_info() -> dict:
    info = read_json(kopia_root / "KOPIA_INFO.json", "Kopia metadata")
    files = info.get("files")
    if not isinstance(files, dict):
        raise PrerequisiteError("KOPIA_INFO.json does not contain a files object")
    missing = [entry for entry in matrix if entry not in files]
    if missing:
        raise PrerequisiteError(f"KOPIA_INFO.json is missing matrix entries: {' '.join(missing)}")
    return info


def agent_binary_name(goos: str, goarch: str) -> str:
    suffix = ".exe" if goos == "windows" else ""
    return f"hfl-agent-{goos}-{goarch}{suffix}"


def user_launcher_binary_name(goos: str, goarch: str) -> str:
    return f"hfl-agent-user-launcher-{goos}-{goarch}.exe"


def package_name(goos: str, goarch: str, flavor: str = "standard") -> str:
    name = f"hfl-agent-{version}-{goos}-{goarch}"
    if flavor in {"ubuntu2004", "ubuntu2204", "ubuntu2404"}:
        name += f"-{flavor}"
    return name


def archive_name(goos: str, goarch: str, flavor: str = "standard") -> str:
    extension = "zip" if goos == "windows" else "tar.gz"
    return f"{package_name(goos, goarch, flavor)}.{extension}"


def executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def manifest_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): f"sha256:{sha256_file(path)}"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "MANIFEST.json"
    }


def write_manifest(root: Path, manifest: dict) -> None:
    manifest["files"] = manifest_files(root)
    (root / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_archive(root: Path, output: Path, goos: str) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    temporary = package_dir / f".{output.name}.tmp-{os.getpid()}"
    temporary.unlink(missing_ok=True)
    try:
        if goos == "windows":
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(root.parent).as_posix())
        else:
            with tarfile.open(temporary, "w:gz") as archive:
                archive.add(root, arcname=root.name)
        os.replace(temporary, output)
        output.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)


def assemble_standard(goos: str, goarch: str, kopia_info: dict) -> None:
    key = f"{goos}:{goarch}"
    platform_dir = work_root / goos / goarch
    agent_source = platform_dir / agent_binary_name(goos, goarch)
    if not agent_source.is_file():
        raise PrerequisiteError(f"missing Agent binary: {agent_source}")

    item = kopia_info["files"][key]
    if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
        raise PrerequisiteError(f"invalid Kopia metadata for {key}")
    kopia_source = kopia_root / item["path"]
    if not kopia_source.is_file():
        raise PrerequisiteError(f"missing Kopia binary: {kopia_source}")
    actual_sha = sha256_file(kopia_source)
    if actual_sha != item["sha256"]:
        raise PrerequisiteError(
            f"Kopia checksum mismatch for {kopia_source}: expected {item['sha256']}, got {actual_sha}"
        )

    log("STEP ", f"Assembling standard Agent archive for {goos}/{goarch}")
    with tempfile.TemporaryDirectory(prefix="hfl-package-") as temp:
        root = Path(temp) / package_name(goos, goarch)
        bin_dir = root / "bin"
        bin_dir.mkdir(parents=True)
        agent_destination = bin_dir / ("hfl-agent.exe" if goos == "windows" else "hfl-agent")
        kopia_destination = bin_dir / ("kopia.exe" if goos == "windows" else "kopia")
        shutil.copy2(agent_source, agent_destination)
        executable(agent_destination)
        if goos == "windows":
            launcher_source = platform_dir / user_launcher_binary_name(goos, goarch)
            if not launcher_source.is_file():
                raise PrerequisiteError(
                    f"missing current-user launcher: {launcher_source}"
                )
            launcher_destination = bin_dir / "hfl-agent-user-launcher.exe"
            shutil.copy2(launcher_source, launcher_destination)
            executable(launcher_destination)
        shutil.copy2(kopia_source, kopia_destination)
        executable(kopia_destination)

        if goos == "windows":
            for filename in ("install.ps1", "install.cmd", "uninstall.cmd"):
                source = install_dir / filename
                if not source.is_file():
                    raise PrerequisiteError(f"missing installer input: {source}")
                shutil.copy2(source, root / filename)
        else:
            source = install_dir / "install.sh"
            if not source.is_file():
                raise PrerequisiteError(f"missing installer input: {source}")
            shutil.copy2(source, root / "install.sh")
            executable(root / "install.sh")
            if goos == "linux":
                if not systemd_unit.is_file():
                    raise PrerequisiteError(f"missing systemd unit: {systemd_unit}")
                if not gateway_lifecycle_script.is_file():
                    raise PrerequisiteError(
                        f"missing Gateway lifecycle script: {gateway_lifecycle_script}"
                    )
                unit_destination = root / "systemd" / systemd_unit.name
                unit_destination.parent.mkdir(parents=True)
                shutil.copy2(systemd_unit, unit_destination)
                lifecycle_destination = root / "libexec" / gateway_lifecycle_script.name
                lifecycle_destination.parent.mkdir(parents=True)
                shutil.copy2(gateway_lifecycle_script, lifecycle_destination)
                executable(lifecycle_destination)

        manifest = {
            "schema": 1,
            "agent_version": version,
            "agent_commit": commit,
            "platform": goos,
            "arch": goarch,
            "bundle_flavor": "standard",
            "kopia_version": kopia_info.get("version", ""),
            "kopia_mode": kopia_info.get("mode", ""),
            "kopia_upstream": f"{kopia_info.get('git_url', '')}@{kopia_info.get('git_ref', '')}",
            "kopia_git_commit": kopia_info.get("git_commit", ""),
            "kopia_patch_sha256": kopia_info.get("patch_sha256", ""),
            "kopia_binary_sha256": f"sha256:{item['sha256']}",
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        write_manifest(root, manifest)
        output = package_dir / archive_name(goos, goarch)
        write_archive(root, output, goos)
    log(" OK  ", f"Wrote {output}")


def safe_extract_tar(archive_path: Path, destination: Path) -> Path:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        roots: set[str] = set()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or member.name.startswith("/") or ".." in parts:
                raise PrerequisiteError(f"unsafe path in base archive: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise PrerequisiteError(f"unsupported entry in base archive: {member.name}")
            roots.add(parts[0])
        if len(roots) != 1:
            raise PrerequisiteError(f"base archive must contain one root directory: {archive_path}")
        # Python 3.12 adds tarfile's data filter. Keep the explicit checks for
        # Python 3.8 hosts, and opt in on newer Python versions so Python 3.14
        # does not emit a deprecation warning during every Dev deployment.
        if sys.version_info >= (3, 12):
            archive.extractall(destination, members=members, filter="data")
        else:
            archive.extractall(destination, members=members)
    return destination / next(iter(roots))


def validate_nas_manifest(ubuntu_release: str, arch: str) -> tuple[Path, dict]:
    source = nas_base / f"ubuntu-{ubuntu_release}" / arch
    manifest = read_json(source / "MANIFEST.json", "NAS dependency manifest")
    if manifest.get("ubuntu_release") != ubuntu_release or manifest.get("arch") != arch:
        raise PrerequisiteError(f"NAS dependency manifest metadata mismatch for {arch}")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise PrerequisiteError(f"NAS dependency manifest has no files for {arch}")
    actual = {
        path.name: f"sha256:{sha256_file(path)}" for path in sorted(source.glob("*.deb"))
    }
    if actual != expected:
        raise PrerequisiteError(f"NAS dependency manifest checksum mismatch for {arch}")
    return source, manifest


def selected_ubuntu_arches() -> list[str]:
    linux_arches = [entry.split(":", 1)[1] for entry in matrix if entry.startswith("linux:")]
    if not ubuntu_arch or ubuntu_arch == "all":
        return linux_arches
    return [ubuntu_arch] if ubuntu_arch in linux_arches else []


def assemble_ubuntu(ubuntu_release: str, arch: str) -> None:
    base = package_dir / archive_name("linux", arch)
    if not base.is_file():
        raise PrerequisiteError(
            f"missing standard archive {base}; use --bundle all or package standard first"
        )
    deps_source, deps_manifest = validate_nas_manifest(ubuntu_release, arch)
    flavor = "ubuntu" + ubuntu_release.replace(".", "")
    log("STEP ", f"Assembling Ubuntu {ubuntu_release} offline Agent archive for linux/{arch}")

    with tempfile.TemporaryDirectory(prefix="hfl-ubuntu-package-") as temp:
        temp_path = Path(temp)
        root = safe_extract_tar(base, temp_path)
        old_manifest_path = root / "MANIFEST.json"
        manifest = read_json(old_manifest_path, "standard package manifest")
        if (
            manifest.get("agent_version") != version
            or manifest.get("agent_commit") != commit
            or manifest.get("platform") != "linux"
            or manifest.get("arch") != arch
        ):
            raise PrerequisiteError(f"standard package manifest mismatch in {base}")

        destination = root / "deps" / flavor / arch
        destination.mkdir(parents=True)
        for filename in sorted(deps_manifest["files"]):
            shutil.copy2(deps_source / filename, destination / filename)

        target_name = package_name("linux", arch, flavor)
        target_root = root.with_name(target_name)
        root.rename(target_root)
        manifest["bundle_flavor"] = flavor
        manifest["nas_deps"] = {
            "ubuntu_release": ubuntu_release,
            "arch": arch,
            "packages": ["nfs-common", "cifs-utils"],
            "source_manifest": f"build/dependencies/agent/ubuntu-{ubuntu_release}/{arch}/MANIFEST.json",
        }
        write_manifest(target_root, manifest)
        output = package_dir / archive_name("linux", arch, flavor)
        write_archive(target_root, output, "linux")
    log(" OK  ", f"Wrote {output}")


try:
    validate_build_info()
    kopia_info = validate_kopia_info()
    if do_standard:
        for entry in matrix:
            assemble_standard(*entry.split(":", 1), kopia_info)
    if ubuntu_releases:
        arches = selected_ubuntu_arches()
        if not arches:
            if bundle in {"ubuntu2004", "ubuntu2204", "ubuntu2404"}:
                raise PrerequisiteError(
                    f"no selected Linux architecture for Ubuntu bundle (matrix={' '.join(matrix)})"
                )
            log("SKIP ", "No Linux platform is selected; skipping Ubuntu archives")
        for ubuntu_release in ubuntu_releases:
            for arch in arches:
                assemble_ubuntu(ubuntu_release, arch)
except PrerequisiteError as exc:
    log("FAIL ", str(exc))
    raise SystemExit(3) from exc
except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
    log("FAIL ", f"Packaging failed: {exc}")
    raise SystemExit(1) from exc

log(" OK  ", f"Agent archives are ready under {package_dir}")
PY
}

validate_matrix
validate_ubuntu_arch
if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
	print_config
	exit 0
fi

command -v python3 >/dev/null 2>&1 || log_fail "python3 is required to package Agent archives" 2
setup_log_file
trap 'exit 130' INT TERM
if [[ "${HFL_PARENT_SESSION:-0}" != "1" ]]; then
	trap finish_session EXIT
	SESSION_STARTED=1
	log_info "Agent packaging session started"
fi
log_info "Bundle: ${BUNDLE}"
log_info "Version: ${AGENT_VERSION}"
log_info "Commit: ${COMMIT}"
log_info "Matrix: ${MATRIX}"
if [[ "${VERBOSE}" -eq 1 ]]; then
	log_info "Ubuntu architecture: ${UBUNTU2404_ARCH:-from matrix}"
	log_info "Build input: ${WORK_ROOT}"
	log_info "NAS dependency input: ${NAS_DEPS_BASE}/ubuntu-{20.04,22.04,24.04}"
	log_info "Output: ${PACKAGE_DIR}"
fi
package_archives
