"""Collect a platform desktop package and create its stable release alias."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


TARGETS = {
    "windows": (".exe", "Quorum-Setup.exe"),
    "linux": (".deb", "quorum.deb"),
    "macos": (".dmg", None),
}

MAC_ARCHES = {"x64", "arm64"}


def collect(
    target: str,
    release_dir: Path,
    output_dir: Path,
    arch: str | None = None,
) -> tuple[Path, Path]:
    extension, fixed_alias = TARGETS[target]
    if target == "macos":
        if arch not in MAC_ARCHES:
            raise SystemExit("macOS artifact collection requires --arch x64 or --arch arm64")
        alias_name = f"Quorum-{arch}.dmg"
    else:
        alias_name = fixed_alias

    candidates = [
        path
        for path in release_dir.glob(f"*{extension}")
        if path.is_file() and path.name.casefold().startswith("quorum-")
    ]
    if target == "macos":
        arch_marker = f"-{arch}."
        candidates = [path for path in candidates if arch_marker in path.name.casefold()]
    if not candidates:
        raise SystemExit(f"No Quorum {extension} package found in {release_dir}")

    # electron-builder writes one top-level package for these single-target
    # commands.  Refuse ambiguity instead of uploading a stale prior build.
    if len(candidates) != 1:
        names = ", ".join(sorted(path.name for path in candidates))
        raise SystemExit(f"Expected exactly one Quorum {extension} package; found: {names}")

    source = candidates[0]
    output_dir.mkdir(parents=True, exist_ok=True)
    versioned = output_dir / source.name
    alias = output_dir / alias_name
    shutil.copy2(source, versioned)
    shutil.copy2(source, alias)
    return versioned, alias


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--arch", choices=sorted(MAC_ARCHES))
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    versioned, alias = collect(args.target, args.release_dir, args.output_dir, args.arch)
    print(f"Collected {versioned.name} and {alias.name}")


if __name__ == "__main__":
    main()
