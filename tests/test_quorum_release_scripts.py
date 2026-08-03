"""Regression coverage for Quorum release artifact assembly."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / "release" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_quorum_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finalize = _load_script("finalize_artifacts").finalize
collect = _load_script("normalize_desktop_artifact").collect


def test_normalize_macos_artifacts_keeps_architectures_distinct(tmp_path: Path):
    output = tmp_path / "output"
    aliases = []

    for arch in ("x64", "arm64"):
        release = tmp_path / arch
        release.mkdir()
        package = release / f"Quorum-0.17.0-mac-{arch}.dmg"
        package.write_bytes(f"dmg-{arch}".encode())

        versioned, alias = collect("macos", release, output, arch)
        assert versioned.name == package.name
        assert versioned.read_bytes() == package.read_bytes()
        assert alias.name == f"Quorum-{arch}.dmg"
        assert alias.read_bytes() == package.read_bytes()
        aliases.append(alias.name)

    assert len(set(aliases)) == 2
    assert sorted(path.name for path in output.glob("*.dmg")) == [
        "Quorum-0.17.0-mac-arm64.dmg",
        "Quorum-0.17.0-mac-x64.dmg",
        "Quorum-arm64.dmg",
        "Quorum-x64.dmg",
    ]


def test_normalize_refuses_ambiguous_stale_packages(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    (release / "Quorum-0.17.0-win-x64.exe").write_bytes(b"first")
    (release / "Quorum-0.17.1-win-x64.exe").write_bytes(b"stale")

    with pytest.raises(SystemExit, match="Expected exactly one"):
        collect("windows", release, tmp_path / "output", "x64")


def test_finalize_covers_every_payload_and_emits_spdx(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    artifacts = tmp_path / "artifacts"
    repo.mkdir()
    artifacts.mkdir()
    (repo / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "quorum-agent", "version": "0.1.0"},
                    "node_modules/example": {
                        "name": "example",
                        "version": "1.2.3",
                        "license": "MIT",
                        "resolved": "https://registry.npmjs.org/example/-/example-1.2.3.tgz",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (repo / "uv.lock").write_text(
        '[[package]]\nname = "example-python"\nversion = "4.5.6"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
        encoding="utf-8",
    )
    payloads = {
        "Quorum-x64.dmg": b"desktop",
        "quorum-plugin.zip": b"companion",
    }
    for name, data in payloads.items():
        (artifacts / name).write_bytes(data)

    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    finalize(repo, artifacts)

    expected_sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(payloads.items())
    )
    assert (artifacts / "SHA256SUMS").read_text(encoding="utf-8") == expected_sums

    sbom = json.loads((artifacts / "Quorum.spdx.json").read_text(encoding="utf-8"))
    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert {entry["fileName"] for entry in sbom["files"]} == {
        "./Quorum-x64.dmg",
        "./quorum-plugin.zip",
    }
    assert {package["name"] for package in sbom["packages"]} >= {
        "Quorum",
        "example",
        "example-python",
    }


def test_release_workflow_is_fail_closed_and_qualified_before_packaging():
    workflow = (REPO_ROOT / ".github" / "workflows" / "quorum-release.yml").read_text(
        encoding="utf-8"
    )

    assert "quorum-v<semver>" in workflow
    assert "needs: [release-context, qualification]" in workflow
    assert "name: ${{ github.ref_type == 'tag' && 'quorum-release' || 'quorum-preview' }}" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "QUORUM_RELEASE_GPG_PRIVATE_KEY" in workflow
    assert "--extra parallel-web" in workflow
    assert "- name: Run the full Desktop test suite\n" in workflow
    assert "run: npm test" in workflow

    unsigned_step = workflow.split("- name: Build unsigned", 1)[1].split(
        "- name: Build signed", 1
    )[0]
    assert "secrets." not in unsigned_step
    assert "CSC_IDENTITY_AUTO_DISCOVERY: false" in unsigned_step


def test_release_workflow_has_complete_non_colliding_platform_matrix():
    workflow = (REPO_ROOT / ".github" / "workflows" / "quorum-release.yml").read_text(
        encoding="utf-8"
    )

    for target, architecture in (
        ("windows", "x64"),
        ("linux", "x64"),
        ("macos-x64", "x64"),
        ("macos-arm64", "arm64"),
    ):
        assert f"target: {target}\n" in workflow
        target_block = workflow.split(f"target: {target}\n", 1)[1].split("- target:", 1)[0]
        assert f"architecture: {architecture}" in target_block

    assert "Build and verify reproducible Quorum Companion" in workflow
    assert "cmp \"$RUNNER_TEMP/quorum-plugin-first.zip\" release-bundle/quorum-plugin.zip" in workflow
