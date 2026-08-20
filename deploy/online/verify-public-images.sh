#!/usr/bin/env bash
# Verify that the complete Community online-install image set is anonymous.
set -euo pipefail

[[ $# -eq 1 && -d "$1" ]] || {
	printf 'Usage: %s METADATA_DIR\n' "$0" >&2
	exit 2
}
metadata_dir=$1
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
docker_config="$(mktemp -d)"
tasks="$(mktemp)"
trap 'rm -rf "${docker_config}"; rm -f "${tasks}"' EXIT

python3 - "${metadata_dir}" "${root}/deploy/online/sourcelens/runtime.json" >"${tasks}" <<'PY'
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
runtime = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", str(runtime.get("git_ref") or "")):
    raise SystemExit("invalid online SourceLens git_ref")
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(runtime.get("version") or "")):
    raise SystemExit("invalid online SourceLens version")
if runtime["git_ref"] != f"v{runtime['version']}":
    raise SystemExit("online SourceLens git_ref and version differ")
if not re.fullmatch(r"[0-9a-f]{40}", str(runtime.get("git_commit") or "")):
    raise SystemExit("invalid online SourceLens git_commit")
selected = {}
sourcelens_components = set()
for path in sorted(root.glob("*.json")):
    metadata = json.loads(path.read_text(encoding="utf-8"))
    component = str(metadata.get("component") or "")
    if component in {"hfl-backend", "hfl-frontend"}:
        # The public installer uses Community HFL images, not the EE pair.
        continue
    if component.startswith("community-hfl-") or component in {
        "postgres",
        "redis",
        "sourcelens-backend",
        "sourcelens-frontend",
        "sourcelens-lensnode",
        "sourcelens-nginx",
        "agent-assets",
        "gateway-assets",
        "language-assets",
    }:
        if component in {
            "sourcelens-backend",
            "sourcelens-frontend",
            "sourcelens-lensnode",
        }:
            for field, expected in {
                "sourcelens_version": runtime["version"],
                "sourcelens_git_ref": runtime["git_ref"],
                "sourcelens_git_commit": runtime["git_commit"],
            }.items():
                if metadata.get(field) != expected:
                    raise SystemExit(
                        f"{component} {field} does not match the online contract"
                    )
            sourcelens_components.add(component)
        digest = str(metadata.get("digest") or "")
        sources = metadata.get("sources") or []
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SystemExit(f"invalid public image digest in {path}")
        regions = {str(source.get("region") or "") for source in sources}
        if len(sources) != 2 or regions != {"cn", "global"}:
            raise SystemExit(f"public image sources are incomplete in {path}")
        for source in sources:
            ref = str(source.get("ref") or "")
            selected[ref] = digest

if sourcelens_components != {
    "sourcelens-backend",
    "sourcelens-frontend",
    "sourcelens-lensnode",
}:
    raise SystemExit("online SourceLens component metadata is incomplete")
if len(selected) != 22:
    raise SystemExit(f"expected 22 regional Community image refs, found {len(selected)}")
for ref, digest in sorted(selected.items()):
    print(f"{ref}\t{digest}")
PY

while IFS=$'\t' read -r ref expected; do
	[[ -n "${ref}" ]] || continue
	printf '[....] Anonymous manifest check: %s\n' "${ref}"
	manifest="$(DOCKER_CONFIG="${docker_config}" timeout 60s \
		docker buildx imagetools inspect "${ref}" --format '{{json .Manifest}}')"
	actual="$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("digest", ""))' \
		<<<"${manifest}")"
	[[ "${actual}" == "${expected}" ]] || {
		printf 'ERROR: public image digest mismatch for %s (%s != %s)\n' \
			"${ref}" "${actual:-missing}" "${expected}" >&2
		exit 1
	}
	printf '[ OK ] Public image available: %s@%s\n' "${ref}" "${actual}"
done <"${tasks}"
