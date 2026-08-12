#!/usr/bin/env python3
"""Materialize HFL_EXTENSION_SOURCES for stack.dev and release image bake.

Prepare stage only: local paths or git URL[+ref]. Runtime reads ``HFL_EXTENSIONS``
(container/local directories). Never clones inside api/web processes.

Modes:
  * compose overlay (``stack.sh``): mount host roots into containers
  * ``--bake-dir`` (``release/build.sh`` / CI): copy into a docker-context tree
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def _split_list(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def _read_id(root: Path) -> str:
    toml = root / "extension.toml"
    if toml.is_file():
        text = toml.read_text(encoding="utf-8")
        m = re.search(r'(?m)^\s*id\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            return m.group(1).strip()
    name = root.name.strip()
    if name.startswith("hyperfilelens-"):
        name = name[len("hyperfilelens-") :]
    return name or "extension"


def _looks_like_root(root: Path) -> bool:
    return (root / "src" / "backend").is_dir()


def _parse_source(item: str) -> tuple[str, str | None]:
    """Return (location, git_ref). location is path or git URL."""
    if item.startswith("git@") or item.startswith("http://") or item.startswith("https://"):
        if "@" in item.rsplit(":", 1)[-1] and not item.startswith("git@"):
            # https://host/repo.git@ref
            url, _, ref = item.rpartition("@")
            return url, ref or None
        if item.startswith("git@") and item.count("@") >= 2:
            # git@host:org/repo.git@ref
            url, _, ref = item.rpartition("@")
            return url, ref or None
        return item, None
    if "://" in item and "@" in item:
        url, _, ref = item.rpartition("@")
        return url, ref or None
    return item, None


def _git_token() -> str:
    return (
        os.environ.get("HFL_EXTENSION_GIT_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )


def _usable_git_token(token: str) -> bool:
    """Reject empty / placeholder values that look set in CI but cannot auth."""
    if len(token) < 20:
        return False
    if token in {"-", "null", "undefined", "NONE", "changeme"}:
        return False
    return True


def _git_env(url: str, *, require_https_auth: bool = False) -> dict[str, str]:
    """Env for git subprocesses: HTTPS auth via GIT_CONFIG_* (not argv / .git/config)."""
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    # Drop inherited Actions checkout auth slots so a packaging token is the only
    # http.*.extraheader applied (GITHUB_TOKEN cannot read private sibling repos).
    for key in list(env):
        if key == "GIT_CONFIG_COUNT" or key.startswith("GIT_CONFIG_KEY_") or key.startswith(
            "GIT_CONFIG_VALUE_"
        ):
            env.pop(key, None)
    token = _git_token()
    if not url.startswith("https://"):
        return env
    parts = urlsplit(url)
    if not parts.hostname or parts.username:
        return env
    if not _usable_git_token(token):
        if require_https_auth:
            raise SystemExit(
                "HTTPS extension source requires a usable HFL_EXTENSION_GIT_TOKEN "
                "(GitHub PAT / fine-grained token with contents:read on the private "
                "extension repo). Placeholder or empty values are rejected."
            )
        return env
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    # Ephemeral config through the environment — avoids token-in-URL remotes and
    # keeps the secret out of `ps` argv (unlike `git -c ...extraheader=...`).
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = f"http.https://{parts.hostname}/.extraheader"
    env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


def _clone(url: str, dest: Path, ref: str | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = _git_env(url, require_https_auth=url.startswith("https://"))
    if dest.exists():
        # Ensure origin stays credential-free (clean up older token-in-URL caches).
        subprocess.check_call(
            ["git", "-C", str(dest), "remote", "set-url", "origin", url],
            stdout=subprocess.DEVNULL,
            env=env,
        )
        subprocess.check_call(
            ["git", "-C", str(dest), "fetch", "--all", "--tags"],
            stdout=subprocess.DEVNULL,
            env=env,
        )
        if ref:
            subprocess.check_call(
                ["git", "-C", str(dest), "checkout", ref],
                stdout=subprocess.DEVNULL,
                env=env,
            )
            subprocess.check_call(
                ["git", "-C", str(dest), "pull", "--ff-only"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        return
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    subprocess.check_call(cmd, env=env)


def materialize(sources: list[str], cache_dir: Path) -> list[Path]:
    roots: list[Path] = []
    for item in sources:
        location, ref = _parse_source(item)
        if location.startswith("git@") or "://" in location:
            base = location.rstrip("/").rsplit("/", 1)[-1]
            if base.endswith(".git"):
                base = base[:-4]
            dest = cache_dir / base
            _clone(location, dest, ref)
            root = dest.resolve()
        else:
            root = Path(location).expanduser().resolve()
        if not _looks_like_root(root):
            raise SystemExit(f"not an extension root (missing src/backend): {root}")
        roots.append(root)
    return roots


def _copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            "node_modules",
            ".venv",
            "dist",
            ".tmp",
            # Never bake local secrets / credentials into release images.
            ".env",
            ".env.*",
            "*.key",
            "*.pem",
            "*.p12",
            "*.pfx",
            "id_rsa",
            "id_rsa.*",
            "credentials.json",
            ".secrets",
            "secrets",
        ),
    )


def bake_extensions(host_roots: list[Path], bake_dir: Path) -> list[tuple[str, Path]]:
    """Copy each root to bake_dir/<id> and return (runtime_path, host_bake_path)."""
    if bake_dir.exists():
        shutil.rmtree(bake_dir)
    bake_dir.mkdir(parents=True, exist_ok=True)
    (bake_dir / ".gitkeep").write_text("", encoding="utf-8")

    mounts: list[tuple[str, Path]] = []
    seen_ids: set[str] = set()
    for root in host_roots:
        ext_id = _read_id(root)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", ext_id):
            raise SystemExit(f"invalid extension id for bake: {ext_id!r}")
        if ext_id in seen_ids:
            raise SystemExit(f"duplicate extension id: {ext_id}")
        seen_ids.add(ext_id)
        staged = bake_dir / ext_id
        _copy_tree(root, staged)
        mounts.append((f"/opt/hfl/extensions/{ext_id}", staged.resolve()))
    return mounts


def write_compose(out: Path, mounts: list[tuple[str, Path]]) -> None:
    """mounts: (container_path, host_path)."""
    lines = [
        "# Generated by tools/extensions/materialize_extensions.py — do not edit.",
        "# Loaded by stack.sh when --extension-source materializes an overlay.",
        "services:",
    ]
    env_val = ",".join(c for c, _ in mounts)
    vol_lines = []
    for container, host in mounts:
        vol_lines.append(f"      - {host}:{container}:ro")
        fe = host / "src" / "frontend" / "src"
        if fe.is_dir():
            vol_lines.append(f"      - {fe}:{container}/src/frontend/src:ro")

    seen: set[str] = set()
    uniq_vols: list[str] = []
    for v in vol_lines:
        if v in seen:
            continue
        seen.add(v)
        uniq_vols.append(v)

    block = [
        "    environment:",
        f'      HFL_EXTENSIONS: "{env_val}"',
        "    volumes:",
        *uniq_vols,
    ]
    for svc in ("migration", "api", "worker", "scheduler"):
        lines.append(f"  {svc}:")
        lines.extend(block)
    lines.append("  web:")
    lines.extend(block)
    lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--sources", default=os.getenv("HFL_EXTENSION_SOURCES", ""))
    parser.add_argument("--extensions", default=os.getenv("HFL_EXTENSIONS", ""))
    parser.add_argument(
        "--compose-out",
        type=Path,
        default=None,
        help="Write docker-compose overlay (default: <repo>/build/docker-compose.extensions.yml)",
    )
    parser.add_argument(
        "--bake-dir",
        type=Path,
        default=None,
        help="Stage extension trees for docker COPY (release/CI). Skips compose overlay.",
    )
    parser.add_argument(
        "--print-extensions",
        action="store_true",
        help="Print runtime HFL_EXTENSIONS value to stdout",
    )
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    cache = repo / "build" / "extensions"
    compose_out = args.compose_out or (repo / "build" / "docker-compose.extensions.yml")
    bake_dir = args.bake_dir.resolve() if args.bake_dir else None

    sources = _split_list(args.sources)
    extensions = _split_list(args.extensions)

    if sources:
        host_roots = materialize(sources, cache)
    elif extensions:
        host_roots = []
        for p in extensions:
            root = Path(p).expanduser().resolve()
            if not _looks_like_root(root):
                raise SystemExit(f"not an extension root: {root}")
            host_roots.append(root)
    else:
        if bake_dir is not None:
            if bake_dir.exists():
                shutil.rmtree(bake_dir)
            bake_dir.mkdir(parents=True, exist_ok=True)
            (bake_dir / ".gitkeep").write_text("", encoding="utf-8")
        elif compose_out.exists():
            compose_out.unlink()
        if args.print_extensions:
            print("")
        return 0

    if bake_dir is not None:
        mounts = bake_extensions(host_roots, bake_dir)
        container_list = ",".join(c for c, _ in mounts)
        if args.print_extensions:
            print(container_list)
        print(f"extensions baked: {len(mounts)} → {bake_dir}", file=sys.stderr)
        for c, h in mounts:
            print(f"  {c} <= {h}", file=sys.stderr)
        return 0

    mounts = []
    seen_ids: set[str] = set()
    for root in host_roots:
        ext_id = _read_id(root)
        if ext_id in seen_ids:
            raise SystemExit(f"duplicate extension id: {ext_id}")
        seen_ids.add(ext_id)
        mounts.append((f"/opt/hfl/extensions/{ext_id}", root))

    write_compose(compose_out, mounts)
    container_list = ",".join(c for c, _ in mounts)
    if args.print_extensions:
        print(container_list)
    print(f"extensions: {len(mounts)} → {compose_out}", file=sys.stderr)
    for c, h in mounts:
        print(f"  {c} <= {h}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
