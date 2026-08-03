"""Distribution identity invariants for Quorum Edition."""

from urllib.parse import urlparse
from pathlib import Path

import product_identity as identity


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_quorum_source_authority_is_not_the_upstream_runtime_repository():
    assert identity.is_quorum_edition()
    assert identity.SOURCE_REPOSITORY != identity.UPSTREAM_REPOSITORY
    assert identity.SOURCE_REPO_URL.endswith(f"/{identity.SOURCE_REPOSITORY}.git")
    assert identity.RAW_SOURCE_BASE_URL.endswith(identity.SOURCE_REPOSITORY)
    parsed = urlparse(identity.SOURCE_REPO_URL)
    canonical = f"{parsed.hostname}/{parsed.path.removesuffix('.git').strip('/')}".lower()
    assert identity.SOURCE_REPO_CANONICAL == canonical


def test_quorum_default_home_cannot_collide_with_stock_hermes():
    assert identity.HOME_DIR_NAME.startswith(".")
    assert identity.HOME_DIR_NAME != ".hermes"
    assert identity.WINDOWS_HOME_DIR_NAME.casefold() != "hermes"


def test_upstream_attribution_remains_factual():
    assert "Hermes Agent" in identity.UPSTREAM_ATTRIBUTION
    assert "Nous Research" in identity.UPSTREAM_ATTRIBUTION


def test_installers_use_only_quorum_source_and_state_authority():
    posix = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    windows = (REPO_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    cmd = (REPO_ROOT / "scripts" / "install.cmd").read_text(encoding="utf-8")

    for installer in (posix, windows, cmd):
        assert "Quorum-Agent/hermes-agent" in installer
        assert "NousResearch/hermes-agent" not in installer
        assert "hermes-agent.nousresearch.com" not in installer

    assert 'HERMES_HOME="$HOME/.quorum"' in posix
    assert "QUORUM_HOME" in posix
    assert "QUORUM_ALLOW_HERMES_HOME_MIGRATION" in posix
    assert "if ($env:QUORUM_HOME)" in windows
    assert 'else { "$env:LOCALAPPDATA\\quorum" }' in windows
    assert "QUORUM_ALLOW_HERMES_HOME_MIGRATION" in windows


def test_managed_installers_refuse_non_quorum_origins_and_verify_runtime_identity():
    posix = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    windows = (REPO_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "assert_quorum_origin" in posix
    assert '"Quorum v"*' in posix
    assert "Assert-QuorumRepositoryOrigin" in windows
    assert 'StartsWith("Quorum v"' in windows


def test_bootstrap_downloads_are_pinned_and_checksum_verified():
    sources = {
        name: (REPO_ROOT / name).read_text(encoding="utf-8")
        for name in (
            "scripts/install.sh",
            "scripts/install.ps1",
            "scripts/install.cmd",
            "scripts/lib/node-bootstrap.sh",
            "hermes_constants.py",
        )
    }

    assert "latest-v" not in "\n".join(sources.values())
    assert "UV_INSTALLER_SHA256" in sources["scripts/install.sh"]
    assert "UvInstallerSha256" in sources["scripts/install.ps1"]
    assert "SHASUMS256.txt" in sources["scripts/install.sh"]
    assert "SHASUMS256.txt" in sources["scripts/install.ps1"]
    assert "SHASUMS256.txt" in sources["scripts/lib/node-bootstrap.sh"]
    assert "SHASUMS256.txt" in sources["hermes_constants.py"]
    assert "Get-AuthenticodeSignature" in sources["scripts/install.ps1"]
    assert "CN=Johannes Schindelin" in sources["scripts/install.ps1"]
    assert "__QUORUM_INSTALL_PS1_SHA256__" not in sources["scripts/install.cmd"]
