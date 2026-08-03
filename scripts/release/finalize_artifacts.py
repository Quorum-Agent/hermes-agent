"""Generate deterministic checksums and an SPDX release SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from product_identity import PRODUCT_NAME, SOURCE_REPOSITORY  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def spdx_id(kind: str, name: str, version: str = "") -> str:
    token = re.sub(r"[^A-Za-z0-9.-]+", "-", f"{name}-{version}").strip("-")
    return f"SPDXRef-{kind}-{token or 'unknown'}"


def dependency_packages(repo_root: Path) -> list[dict]:
    found: dict[tuple[str, str, str], dict] = {}

    package_lock = json.loads((repo_root / "package-lock.json").read_text(encoding="utf-8"))
    for lock_path, metadata in package_lock.get("packages", {}).items():
        if not lock_path or not isinstance(metadata, dict):
            continue
        name = metadata.get("name") or Path(lock_path).name
        version = str(metadata.get("version") or "")
        if not name or not version or metadata.get("link"):
            continue
        key = ("npm", str(name), version)
        found[key] = {
            "SPDXID": spdx_id("npm", str(name), version),
            "name": str(name),
            "versionInfo": version,
            "downloadLocation": metadata.get("resolved", "NOASSERTION"),
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": metadata.get("license", "NOASSERTION"),
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": f"pkg:npm/{quote(str(name), safe='@/') }@{quote(version)}",
                }
            ],
        }

    with (repo_root / "uv.lock").open("rb") as stream:
        uv_lock = tomllib.load(stream)
    for metadata in uv_lock.get("package", []):
        name = str(metadata.get("name") or "")
        version = str(metadata.get("version") or "")
        if not name or not version:
            continue
        key = ("pypi", name, version)
        source = metadata.get("source") or {}
        registry = source.get("registry") if isinstance(source, dict) else None
        found[key] = {
            "SPDXID": spdx_id("pypi", name, version),
            "name": name,
            "versionInfo": version,
            "downloadLocation": registry or "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": f"pkg:pypi/{quote(name)}@{quote(version)}",
                }
            ],
        }

    return [found[key] for key in sorted(found)]


def finalize(repo_root: Path, artifact_dir: Path) -> None:
    excluded = {"SHA256SUMS", "SHA256SUMS.asc", "Quorum.spdx.json"}
    artifacts = sorted(path for path in artifact_dir.iterdir() if path.is_file() and path.name not in excluded)
    if not artifacts:
        raise SystemExit(f"No release artifacts found in {artifact_dir}")

    hashes = {path.name: sha256(path) for path in artifacts}
    sums = "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
    (artifact_dir / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")

    revision = os.environ.get("GITHUB_SHA", "unknown")
    namespace_revision = revision if re.fullmatch(r"[0-9a-fA-F]{40}", revision) else hashlib.sha256(revision.encode()).hexdigest()
    root_id = "SPDXRef-Package-Quorum"
    packages = dependency_packages(repo_root)
    files = [
        {
            "SPDXID": spdx_id("File", path.name),
            "fileName": f"./{path.name}",
            "checksums": [{"algorithm": "SHA256", "checksumValue": hashes[path.name]}],
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
        for path in artifacts
    ]
    relationships = [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": root_id}]
    relationships.extend(
        {"spdxElementId": root_id, "relationshipType": "DEPENDS_ON", "relatedSpdxElement": package["SPDXID"]}
        for package in packages
    )
    relationships.extend(
        {"spdxElementId": root_id, "relationshipType": "GENERATES", "relatedSpdxElement": file["SPDXID"]}
        for file in files
    )

    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{PRODUCT_NAME} release",
        "documentNamespace": f"https://github.com/{SOURCE_REPOSITORY}/sbom/{namespace_revision}",
        "creationInfo": {
            "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "creators": ["Tool: Quorum-release-finalizer/1"],
        },
        "packages": [
            {
                "SPDXID": root_id,
                "name": PRODUCT_NAME,
                "versionInfo": revision,
                "downloadLocation": f"https://github.com/{SOURCE_REPOSITORY}",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "MIT",
            },
            *packages,
        ],
        "files": files,
        "relationships": relationships,
    }
    (artifact_dir / "Quorum.spdx.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    finalize(args.repo_root.resolve(), args.artifact_dir.resolve())


if __name__ == "__main__":
    main()
