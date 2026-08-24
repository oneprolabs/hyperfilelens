#!/usr/bin/env python3
"""Prepare a registry-backed Community package from one immutable HFL tag."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any


VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
GLOBAL_PREFIX = os.environ.get(
    "HFL_GLOBAL_REGISTRY_PREFIX", "docker.io/oneprolabs"
).rstrip("/")
CN_PREFIX = os.environ.get(
    "HFL_CN_REGISTRY_PREFIX",
    "registry.cn-beijing.aliyuncs.com/oneprolabs",
).rstrip("/")


@dataclass(frozen=True)
class ImageSpec:
    """Describe one published image and its local runtime identity."""

    component: str
    role: str
    repository: str
    tag: str
    asset_kind: str = ""

    @property
    def local_ref(self) -> str:
        """Return the registry-independent image reference used by Compose."""
        return f"{self.repository}:{self.tag}"

    def source_ref(self, region: str) -> str:
        """Return the published source reference for a registry region."""
        prefix = CN_PREFIX if region == "cn" else GLOBAL_PREFIX
        return f"{prefix}/{self.repository}:{self.tag}"


@dataclass(frozen=True)
class ResolvedImage:
    """Hold immutable metadata for one successfully resolved image."""

    spec: ImageSpec
    digest: str

    def manifest_entry(self) -> dict[str, Any]:
        """Return the normalized registry delivery manifest entry."""
        entry: dict[str, Any] = {
            "component": self.spec.component,
            "role": self.spec.role,
            "local_ref": self.spec.local_ref,
            "digest": self.digest,
            "platform": "linux/amd64",
            "sources": [
                {"region": "cn", "ref": self.spec.source_ref("cn")},
                {"region": "global", "ref": self.spec.source_ref("global")},
            ],
        }
        if self.spec.asset_kind:
            entry["asset_kind"] = self.spec.asset_kind
        return entry


def run(
    command: list[str],
    *,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with stable text handling and useful diagnostics."""
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.STDOUT if capture_output else None,
    )
    if check and completed.returncode != 0:
        output = (completed.stdout or "").strip()
        detail = f": {output[-1000:]}" if output else ""
        raise RuntimeError(f"command failed ({' '.join(command)}){detail}")
    return completed


def sha256_file(path: pathlib.Path) -> str:
    """Return the SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: pathlib.Path) -> str:
    """Return a deterministic digest for regular files below a directory."""
    digest = hashlib.sha256()
    for path in sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    ):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def replace_env_values(path: pathlib.Path, values: dict[str, str]) -> None:
    """Replace or append environment keys without evaluating the template."""
    text = path.read_text(encoding="utf-8")
    for name, value in values.items():
        pattern = re.compile(rf"^{re.escape(name)}=.*$", re.MULTILINE)
        line = f"{name}={value}"
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            text = text.rstrip() + f"\n{name}={value}\n"
    path.write_text(text, encoding="utf-8")


def copy_runtime_files(
    source: pathlib.Path, target: pathlib.Path, version: str
) -> None:
    """Stage the HFL and SourceLens runtime configuration from the Git tag."""
    target.mkdir(parents=True)
    (target / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    shutil.copy2(source / "deploy/docker-compose.yml", target / "docker-compose.yml")
    shutil.copy2(source / ".env.example", target / ".env.example")
    replace_env_values(
        target / ".env.example",
        {
            "APP_VERSION": version,
            "HFL_PRODUCT_VERSION": version,
            "HFL_EDITION": "community",
            "HFL_BACKEND_IMAGE": f"hyperfilelens-backend:{version}",
            "HFL_FRONTEND_IMAGE": f"hyperfilelens-frontend:{version}",
            "HFL_GATEWAY_VERSION": version,
            "HFL_RELEASE_CHANNEL": "release",
            "AGENT_VERSION": version,
        },
    )

    copies = {
        "deploy/installer/install.sh": "install.sh",
        "deploy/installer/compose-runtime.sh": "payload/runtime/compose-runtime.sh",
        "deploy/installer/apply-runtime-config.py": "apply-runtime-config.py",
        "tools/config/sync_env.py": "sync-env.py",
        "LICENSE": "LICENSE",
    }
    for source_name, target_name in copies.items():
        destination = target / target_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / source_name, destination)
        executable = target_name in {
            "install.sh",
            "apply-runtime-config.py",
            "sync-env.py",
        }
        destination.chmod(0o755 if executable else 0o644)

    for relative in (
        "deploy/nginx/certs",
        "deploy/nginx/snippets",
        "deploy/blue-green",
        "deploy/logrotate",
    ):
        shutil.copytree(source / relative, target / relative, dirs_exist_ok=True)
    for name in ("default.conf", "web.conf"):
        destination = target / "deploy/nginx" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "deploy/nginx" / name, destination)
    (target / "images").mkdir()
    (target / "host").mkdir()
    (target / "payload/media").mkdir(parents=True)
    (target / "payload/language-packs").mkdir(parents=True)


def render_sourcelens_compose(
    template: pathlib.Path,
    destination: pathlib.Path,
    version: str,
) -> None:
    """Render the shared SourceLens Compose template with HFL-versioned refs."""
    text = template.read_text(encoding="utf-8")
    for block in ("EMBED_BACKEND_ENV", "EMBED_LENSNODE_SERVICE"):
        pattern = re.compile(rf"(?ms)^# HFL_{block}_BEGIN\n(.*?)^# HFL_{block}_END\n")
        if len(pattern.findall(text)) != 1:
            raise ValueError(f"SourceLens template has an invalid {block} block")
        text = pattern.sub("", text)
    replacements = {
        "__SOURCELENS_BACKEND_IMAGE__": (f"hyperfilelens-sourcelens-backend:{version}"),
        "__SOURCELENS_FRONTEND_IMAGE__": (
            f"hyperfilelens-sourcelens-frontend:{version}"
        ),
        "__SOURCELENS_LENSNODE_IMAGE__": (
            f"hyperfilelens-sourcelens-lensnode:{version}"
        ),
        "__SOURCELENS_CONSOLE_BIND_ADDRESS__": "0.0.0.0",
        "__SOURCELENS_CONSOLE_PORT__": "11445",
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    if "__SOURCELENS_" in text or "HFL_EMBED_" in text:
        raise ValueError("SourceLens Compose template has unresolved markers")
    destination.write_text(text, encoding="utf-8")


def stage_sourcelens(source: pathlib.Path, target: pathlib.Path, version: str) -> None:
    """Stage the repository-owned SourceLens runtime tree."""
    online = source / "deploy/online/sourcelens"
    root = target / "sourcelens"
    (root / "deploy/nginx/hfl-maintenance").mkdir(parents=True)
    (root / "deploy/postgresql").mkdir(parents=True)
    (root / "deploy/sentry").mkdir(parents=True)

    shutil.copy2(online / "env.example", root / ".env.example")
    run(
        [
            sys.executable,
            str(source / "deploy/installer/sourcelens/patch-env-runtime.py"),
            str(root / ".env.example"),
        ]
    )
    replace_env_values(
        root / ".env.example",
        {
            "SOURCELENS_CONSOLE_BIND_ADDRESS": "0.0.0.0",
            "SOURCELENS_CONSOLE_PORT": "11445",
            "NGINX_HTTPS_PORT": "11445",
        },
    )
    render_sourcelens_compose(
        source / "deploy/installer/sourcelens/docker-compose.template.yml",
        root / "docker-compose.yml",
        version,
    )
    shutil.copy2(online / "nginx/default.conf", root / "deploy/nginx/default.conf")
    shutil.copytree(
        online / "postgresql", root / "deploy/postgresql", dirs_exist_ok=True
    )

    runtime_files = {
        "deploy/installer/sourcelens/install.sh": "install.sh",
        "deploy/installer/sourcelens/compose-lifecycle.sh": "compose-lifecycle.sh",
        "deploy/installer/sourcelens/patch-env-runtime.py": "patch-env-runtime.py",
        "deploy/installer/sourcelens/sync-sentry-runtime.py": "sync-sentry-runtime.py",
        "deploy/installer/sourcelens/hfl-sentry-loader.js": (
            "deploy/nginx/hfl-sentry-loader.js"
        ),
        "deploy/installer/sourcelens/run-creation-gate-off.conf": (
            "deploy/nginx/hfl-maintenance/run-creation-gate.conf"
        ),
        "deploy/installer/sourcelens/hfl-sentry-sitecustomize.py": (
            "deploy/sentry/hfl-sentry-sitecustomize.py"
        ),
    }
    for source_name, target_name in runtime_files.items():
        destination = root / target_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / source_name, destination)
    for name in (
        "install.sh",
        "compose-lifecycle.sh",
        "patch-env-runtime.py",
        "sync-sentry-runtime.py",
    ):
        (root / name).chmod(0o755)
    (root / "deploy/nginx/hfl-sentry-config.js").write_text(
        "window.__HFL_SOURCELENS_SENTRY__ = Object.freeze({ enabled: false })\n",
        encoding="utf-8",
    )


def image_digest(ref: str) -> str:
    """Read the immutable repository digest retained by Docker pull."""
    completed = run(
        ["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"],
        capture_output=True,
    )
    values = json.loads((completed.stdout or "[]").strip())
    repository_path = ref.rsplit(":", 1)[0].split("/", 1)[-1]
    digests = {
        value.rsplit("@", 1)[-1]
        for value in values
        if isinstance(value, str)
        and "@" in value
        and value.rsplit("@", 1)[0].endswith(repository_path)
    }
    valid = sorted(value for value in digests if DIGEST_PATTERN.fullmatch(value))
    if len(valid) != 1:
        raise ValueError(f"image {ref} has ambiguous repository digests: {valid}")
    return valid[0]


def resolve_image(spec: ImageSpec, preferred_region: str) -> ResolvedImage:
    """Pull one public image with regional fallback and retain its local alias."""
    fallback = "global" if preferred_region == "cn" else "cn"
    failures: list[str] = []
    for region in (preferred_region, fallback):
        source_ref = spec.source_ref(region)
        print(f"[....] Pulling {source_ref}", flush=True)
        completed = run(
            ["docker", "pull", "--platform", "linux/amd64", source_ref],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            failures.append(f"{source_ref}: {(completed.stdout or '').strip()[-500:]}")
            continue
        digest = image_digest(source_ref)
        run(["docker", "tag", source_ref, spec.local_ref])
        print(f"[ OK ] Resolved {spec.local_ref}@{digest}", flush=True)
        return ResolvedImage(spec=spec, digest=digest)
    raise RuntimeError(
        f"neither public registry could provide {spec.local_ref}: "
        + "; ".join(failures)
    )


def image_revision(ref: str) -> str:
    """Return and validate the source revision embedded in an HFL image."""
    completed = run(
        [
            "docker",
            "image",
            "inspect",
            ref,
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        ],
        capture_output=True,
    )
    revision = (completed.stdout or "").strip().lower()
    if not REVISION_PATTERN.fullmatch(revision):
        raise ValueError(f"image {ref} has no valid source revision label")
    return revision


def validate_asset_tree(root: pathlib.Path, kind: str) -> None:
    """Reject links and paths outside the declared asset payload roots."""
    prefixes = {
        "agent": (
            pathlib.PurePosixPath("payload/media/agent-releases"),
            pathlib.PurePosixPath("payload/media/enroll-bootstrap"),
        ),
        "gateway": (pathlib.PurePosixPath("payload/media/gateway-bootstrap"),),
        "language": (pathlib.PurePosixPath("payload/language-packs"),),
    }[kind]
    marker = root / ".asset-kind"
    if marker.read_text(encoding="utf-8").strip() != kind:
        raise ValueError(f"{kind} asset image has an invalid marker")
    for prefix in prefixes:
        directory = root / prefix
        if not directory.is_dir() or not any(directory.iterdir()):
            raise ValueError(f"{kind} asset image is missing {prefix}")
    allowed_ancestors = {pathlib.PurePosixPath(".asset-kind")}
    for prefix in prefixes:
        allowed_ancestors.update(prefix.parents)
        allowed_ancestors.add(prefix)
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError(f"{kind} asset contains unsupported entry: {path}")
        relative = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        if relative in allowed_ancestors:
            continue

        def is_under(prefix: pathlib.PurePosixPath) -> bool:
            try:
                relative.relative_to(prefix)
            except ValueError:
                return False
            return True

        if not any(is_under(prefix) for prefix in prefixes):
            raise ValueError(f"{kind} asset contains unexpected path: {relative}")


def extract_asset(image: ResolvedImage, payload_root: pathlib.Path) -> None:
    """Copy and validate one scratch asset image into the local package."""
    kind = image.spec.asset_kind
    container = run(
        ["docker", "create", image.spec.local_ref, "/bin/true"],
        capture_output=True,
    ).stdout
    container_id = (container or "").strip()
    if not container_id:
        raise RuntimeError(f"could not create {kind} asset container")
    try:
        with tempfile.TemporaryDirectory(prefix=f"hfl-{kind}-asset-") as temporary:
            root = pathlib.Path(temporary)
            run(
                [
                    "docker",
                    "cp",
                    f"{container_id}:/opt/hyperfilelens-assets/.",
                    str(root),
                ]
            )
            validate_asset_tree(root, kind)
            shutil.copytree(root / "payload", payload_root, dirs_exist_ok=True)
    finally:
        run(["docker", "rm", "-f", container_id], check=False)


def discard_asset_image(image: ResolvedImage) -> None:
    """Remove scratch asset tags after their payload has been materialized."""
    refs = {
        image.spec.local_ref,
        image.spec.source_ref("cn"),
        image.spec.source_ref("global"),
    }
    for ref in sorted(refs):
        run(["docker", "image", "rm", ref], check=False)


def write_sourcelens_build_info(
    source: pathlib.Path,
    target: pathlib.Path,
    runtime: list[ResolvedImage],
) -> dict[str, Any]:
    """Write the semantic SourceLens bundle identity used by upgrades."""
    base = json.loads(
        (source / "deploy/online/sourcelens/runtime.json").read_text(encoding="utf-8")
    )
    by_component = {image.spec.component: image for image in runtime}
    template_root = source / "deploy/online/sourcelens"
    info = {
        "enabled": True,
        "git_url": "https://github.com/oneprolabs/sourcelens.git",
        "git_ref": base["git_ref"],
        "git_commit": base["git_commit"],
        "git_commit_short": base["git_commit"][:7],
        "version": base["version"],
        "patchset_sha256": sha256_tree(template_root),
        "patches": [],
        "build_adapter_sha256": sha256_file(source / "deploy/online/prepare.py"),
        "build_compose_file": "docker-compose.standalone.yml",
        "network": "hyperfilelens-bridge",
        "install_dir": "/opt/hyperfilelens/sourcelens",
        "lensnode_image": by_component["sourcelens-lensnode"].spec.local_ref,
        "embed_local_lensnode": False,
        "images": {
            name: {
                "ref": by_component[f"sourcelens-{name}"].spec.local_ref,
                "upstream_ref": by_component[f"sourcelens-{name}"].spec.source_ref(
                    "global"
                ),
                "digest": by_component[f"sourcelens-{name}"].digest,
            }
            for name in ("backend", "frontend", "lensnode")
        },
    }
    nginx = by_component["sourcelens-nginx"]
    info["images"]["nginx"] = {
        "ref": nginx.spec.local_ref,
        "digest": nginx.digest,
    }
    (target / "sourcelens/BUILD_INFO.json").write_text(
        json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return info


def write_manifest(
    target: pathlib.Path,
    version: str,
    revision: str,
    runtime: list[ResolvedImage],
    assets: list[ResolvedImage],
    sourcelens: dict[str, Any],
) -> None:
    """Write the local immutable installation manifest."""
    by_component = {image.spec.component: image for image in runtime}
    images = [
        {
            "role": "hyperfilelens",
            "refs": [
                by_component["hfl-backend"].spec.local_ref,
                by_component["hfl-frontend"].spec.local_ref,
            ],
            "digests": [
                by_component["hfl-backend"].digest,
                by_component["hfl-frontend"].digest,
            ],
        }
    ]
    images.extend(
        {
            "role": image.spec.role,
            "refs": [image.spec.local_ref],
            "digests": [image.digest],
        }
        for image in runtime
        if image.spec.component not in {"hfl-backend", "hfl-frontend"}
    )
    manifest = {
        "schema_version": 3,
        "product": "hyperfilelens",
        "edition": "community",
        "channel": "release",
        "artifact_id": f"v{version}",
        "version": version,
        "image_version": version,
        "built_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "minimum_upgrade_version": "0.1.34",
        "git_commit": revision,
        "runtime_images": {
            "backend": by_component["hfl-backend"].spec.local_ref,
            "frontend": by_component["hfl-frontend"].spec.local_ref,
        },
        "host_runtime": {
            "os_id": "ubuntu",
            "os_versions": ["20.04", "22.04", "24.04"],
            "arch": "amd64",
            "docker": {
                "min_engine_version": "24.0.0",
                "min_compose_version": "2.20.0",
            },
        },
        "sourcelens": sourcelens,
        "images": images,
        "delivery": {
            "mode": "registry",
            "registry_images": [image.manifest_entry() for image in runtime],
            "asset_images": [image.manifest_entry() for image in assets],
        },
        "artifacts": {"agent_version": version},
    }
    (target / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=pathlib.Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--region", choices=("cn", "global"), required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def main() -> int:
    """Build a temporary Community package and resolve all public images."""
    args = parse_args()
    source = args.source_root.resolve(strict=True)
    version = args.version[1:] if args.version.startswith("v") else args.version
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"invalid HFL version: {args.version}")
    target = args.output.resolve()
    if target.exists() or target.is_symlink():
        raise ValueError(f"online package output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    copy_runtime_files(source, target, version)
    stage_sourcelens(source, target, version)

    runtime_specs = [
        ImageSpec("hfl-backend", "hyperfilelens", "hyperfilelens-backend", version),
        ImageSpec("hfl-frontend", "hyperfilelens", "hyperfilelens-frontend", version),
        ImageSpec(
            "sourcelens-backend",
            "sourcelens-backend",
            "hyperfilelens-sourcelens-backend",
            version,
        ),
        ImageSpec(
            "sourcelens-frontend",
            "sourcelens-frontend",
            "hyperfilelens-sourcelens-frontend",
            version,
        ),
        ImageSpec(
            "sourcelens-lensnode",
            "sourcelens-lensnode",
            "hyperfilelens-sourcelens-lensnode",
            version,
        ),
        ImageSpec(
            "sourcelens-nginx",
            "sourcelens-nginx",
            "hyperfilelens-sourcelens-nginx",
            "stable-alpine",
        ),
        ImageSpec("postgres", "shared", "hyperfilelens-postgres", "17"),
        ImageSpec("redis", "shared", "hyperfilelens-redis", "alpine"),
    ]
    asset_specs = [
        ImageSpec(
            f"{kind}-assets",
            f"{kind}-assets",
            f"hyperfilelens-{kind}-assets",
            version,
            kind,
        )
        for kind in ("agent", "gateway", "language")
    ]
    runtime = [resolve_image(spec, args.region) for spec in runtime_specs]
    assets = [resolve_image(spec, args.region) for spec in asset_specs]

    revision = image_revision(f"hyperfilelens-backend:{version}")
    if image_revision(f"hyperfilelens-frontend:{version}") != revision:
        raise ValueError("Community backend and frontend revisions do not match")
    for asset in assets:
        try:
            extract_asset(asset, target / "payload")
        finally:
            discard_asset_image(asset)
    sourcelens = write_sourcelens_build_info(source, target, runtime)
    write_manifest(target, version, revision, runtime, assets, sourcelens)
    print(f"[ OK ] Prepared Community package: {target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        raise SystemExit(1) from error
