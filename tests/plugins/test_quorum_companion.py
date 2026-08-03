"""Build/install behavior for the stock-Hermes Quorum Companion bundle."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_companion_archive_is_reproducible_and_self_verifying(tmp_path):
    build = _load("test_quorum_companion_build", REPO_ROOT / "quorum-companion" / "build.py")
    first = build.build_archive(tmp_path / "first.zip")
    second = build.build_archive(tmp_path / "second.zip")

    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        names = set(archive.namelist())
        assert "quorum-plugin/plugins/quorum/plugin.yaml" in names
        assert "quorum-plugin/plugins/quorum/dashboard/plugin_api.py" in names
        assert "quorum-plugin/desktop-plugins/quorum/plugin.js" in names
        assert "quorum-plugin/install.py" in names
        sums = archive.read("quorum-plugin/SHA256SUMS").decode("utf-8").splitlines()
        for line in sums:
            expected, relative = line.split("  ", 1)
            actual = hashlib.sha256(archive.read(f"quorum-plugin/{relative}")).hexdigest()
            assert actual == expected


def test_companion_installer_preflights_collisions_and_installs_both_surfaces(tmp_path):
    build = _load("test_quorum_companion_build_install", REPO_ROOT / "quorum-companion" / "build.py")
    archive_path = build.build_archive(tmp_path / "quorum-plugin.zip")
    extracted = tmp_path / "extracted"
    with ZipFile(archive_path) as archive:
        archive.extractall(extracted)

    bundle = extracted / "quorum-plugin"
    installer = _load("test_quorum_companion_installer", bundle / "install.py")
    hermes_home = tmp_path / "hermes-home"
    targets = installer.install_bundle(bundle, hermes_home)

    assert targets == [hermes_home / "plugins" / "quorum", hermes_home / "desktop-plugins" / "quorum"]
    assert (targets[0] / "plugin.yaml").is_file()
    assert (targets[0] / "dashboard" / "plugin_api.py").is_file()
    assert (targets[1] / "plugin.js").is_file()
    with pytest.raises(FileExistsError):
        installer.install_bundle(bundle, hermes_home)


def test_companion_installer_keeps_committed_update_if_backup_cleanup_fails(
    tmp_path, monkeypatch
):
    build = _load(
        "test_quorum_companion_build_cleanup",
        REPO_ROOT / "quorum-companion" / "build.py",
    )
    archive_path = build.build_archive(tmp_path / "quorum-plugin.zip")
    extracted = tmp_path / "extracted"
    with ZipFile(archive_path) as archive:
        archive.extractall(extracted)

    bundle = extracted / "quorum-plugin"
    installer = _load("test_quorum_companion_installer_cleanup", bundle / "install.py")
    hermes_home = tmp_path / "hermes-home"
    old_plugin = hermes_home / "plugins" / "quorum"
    old_desktop = hermes_home / "desktop-plugins" / "quorum"
    old_plugin.mkdir(parents=True)
    old_desktop.mkdir(parents=True)
    (old_plugin / "old.txt").write_text("old", encoding="utf-8")
    (old_desktop / "old.txt").write_text("old", encoding="utf-8")

    original_rmtree = installer.shutil.rmtree

    def fail_backup_cleanup(path):
        if ".backup-" in Path(path).name:
            raise OSError("simulated locked backup")
        return original_rmtree(path)

    monkeypatch.setattr(installer.shutil, "rmtree", fail_backup_cleanup)
    targets = installer.install_bundle(bundle, hermes_home, replace=True)

    assert (targets[0] / "plugin.yaml").is_file()
    assert (targets[1] / "plugin.js").is_file()
    assert not (targets[0] / "old.txt").exists()
    assert not (targets[1] / "old.txt").exists()
    assert list((hermes_home / "plugins").glob(".quorum.backup-*"))
    assert list((hermes_home / "desktop-plugins").glob(".quorum.backup-*"))
