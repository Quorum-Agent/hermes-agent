"""Install Quorum Companion into one Hermes home."""

from __future__ import annotations

import argparse
import os
import shutil
import uuid
from contextlib import suppress
from pathlib import Path


def default_hermes_home() -> Path:
    configured = os.environ.get("HERMES_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".hermes"


def _assert_scoped(target: Path, hermes_home: Path) -> None:
    try:
        target.resolve().relative_to(hermes_home.resolve())
    except ValueError as exc:
        raise ValueError(f"refusing target outside Hermes home: {target}") from exc


def install_bundle(bundle_root: Path, hermes_home: Path, *, replace: bool = False, dry_run: bool = False) -> list[Path]:
    bundle_root = bundle_root.resolve()
    hermes_home = hermes_home.expanduser().resolve()
    sources = [
        bundle_root / "plugins" / "quorum",
        bundle_root / "desktop-plugins" / "quorum",
    ]
    targets = [
        hermes_home / "plugins" / "quorum",
        hermes_home / "desktop-plugins" / "quorum",
    ]

    for source in sources:
        if not source.is_dir():
            raise FileNotFoundError(f"bundle payload missing: {source}")
    for target in targets:
        _assert_scoped(target, hermes_home)
        if target.exists() and not replace:
            raise FileExistsError(f"already installed: {target}; pass --replace to update")

    if dry_run:
        return targets

    transaction = uuid.uuid4().hex
    staged: list[Path] = []
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for source, target in zip(sources, targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.parent / f".{target.name}.installing-{transaction}"
            _assert_scoped(stage, hermes_home)
            shutil.copytree(source, stage)
            staged.append(stage)

        for stage, target in zip(staged, targets):
            if target.exists():
                backup = target.parent / f".{target.name}.backup-{transaction}"
                _assert_scoped(backup, hermes_home)
                target.replace(backup)
                backups.append((target, backup))
            stage.replace(target)
            installed.append(target)

        # Both targets are now committed. Backup cleanup must never turn a
        # successful install into a destructive rollback after an earlier
        # backup was already removed; an undeletable backup is safer residue.
        for _, backup in backups:
            with suppress(OSError):
                shutil.rmtree(backup)
        return targets
    except Exception:
        for target in reversed(installed):
            if target.is_dir():
                shutil.rmtree(target)
        for target, backup in reversed(backups):
            if backup.exists():
                backup.replace(target)
        for stage in staged:
            if stage.is_dir():
                shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--replace", action="store_true", help="Transactionally replace an existing Companion")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print targets without writing")
    args = parser.parse_args()
    targets = install_bundle(Path(__file__).resolve().parent, args.hermes_home, replace=args.replace, dry_run=args.dry_run)
    verb = "Would install" if args.dry_run else "Installed"
    for target in targets:
        print(f"{verb}: {target}")
    if not args.dry_run:
        print("Next: run `hermes plugins enable quorum`, then restart Hermes Desktop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
