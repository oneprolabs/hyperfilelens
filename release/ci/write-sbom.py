#!/usr/bin/env python3
"""Create a compact SPDX 2.3 document from an HFL release manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    version = str(manifest.get("artifact_id") or manifest["version"])
    commit = str(manifest["git_commit"])
    namespace_seed = hashlib.sha256(
        f"hyperfilelens:{version}:{commit}".encode()
    ).hexdigest()

    packages = [
        {
            "SPDXID": "SPDXRef-Package-HyperFileLens",
            "name": "hyperfilelens",
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "OTHER",
                    "referenceType": "vcs",
                    "referenceLocator": (
                        "https://github.com/oneprolabs/hyperfilelens@" + commit
                    ),
                }
            ],
        }
    ]
    relationships = []
    files = []
    for index, image in enumerate(manifest.get("images", []), start=1):
        image_id = f"SPDXRef-ContainerImage-{index}"
        refs = image.get("refs") or []
        digests = image.get("digests") or []
        packages.append(
            {
                "SPDXID": image_id,
                "name": refs[0] if refs else str(image.get("file", "image")),
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": image["sha256"]}
                ],
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:docker/{refs[0]}",
                    }
                    for _ in refs[:1]
                ],
                "comment": "Registry/image digests: " + ", ".join(digests),
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-HyperFileLens",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": image_id,
            }
        )

    default_tls = (manifest.get("artifacts") or {}).get("default_tls") or {}
    for role, artifact in sorted(default_tls.items()):
        file_id = "SPDXRef-File-DefaultTLS-" + role.replace("_", "-")
        files.append(
            {
                "SPDXID": file_id,
                "fileName": str(artifact["file"]),
                "checksums": [
                    {
                        "algorithm": "SHA256",
                        "checksumValue": str(artifact["sha256"]),
                    }
                ],
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "comment": "Repository-pinned HyperFileLens default TLS " + role,
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-HyperFileLens",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file_id,
            }
        )

    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"hyperfilelens-{version}",
        "documentNamespace": (
            "https://github.com/oneprolabs/hyperfilelens/releases/"
            f"spdx/{version}/{namespace_seed}"
        ),
        "creationInfo": {
            "created": dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "creators": ["Tool: HyperFileLens release/ci/write-sbom.py"],
        },
        "documentDescribes": ["SPDXRef-Package-HyperFileLens"],
        "packages": packages,
        "files": files,
        "relationships": relationships,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
