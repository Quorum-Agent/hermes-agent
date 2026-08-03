"""Build the deterministic Quorum Companion ZIP."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Iterator
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
ARCHIVE_ROOT = "quorum-plugin"
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _tree(root: Path, archive_prefix: str) -> Iterator[tuple[str, bytes]]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix()
        yield f"{archive_prefix}/{relative}", path.read_bytes()


def payload() -> list[tuple[str, bytes]]:
    files = [
        ("README.md", (HERE / "package" / "README.md").read_bytes()),
        ("NOTICE", (HERE / "package" / "NOTICE").read_bytes()),
        ("install.py", (HERE / "package" / "install.py").read_bytes()),
        ("HERMES-LICENSE", (REPO_ROOT / "LICENSE").read_bytes()),
        ("desktop-plugins/quorum/plugin.js", (HERE / "desktop" / "plugin.js").read_bytes()),
    ]
    files.extend(_tree(REPO_ROOT / "plugins" / "quorum", "plugins/quorum"))
    return sorted(files)


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(f"{ARCHIVE_ROOT}/{name}", FIXED_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_archive(output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = payload()
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files).encode("utf-8")

    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in [*files, ("SHA256SUMS", sums)]:
            archive.writestr(_zip_info(name), data)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "dist" / "quorum-plugin.zip",
        help="Archive destination (default: dist/quorum-plugin.zip)",
    )
    args = parser.parse_args()
    archive = build_archive(args.output)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

