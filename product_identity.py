"""Quorum Edition product and source identity.

This module is deliberately stdlib-only and import-safe.  Runtime code uses it
to distinguish the Quorum distribution from upstream Hermes without scattering
repository, product, and state-root literals through security-sensitive paths.

Hermes remains the upstream agent runtime and receives factual attribution in
the product metadata.  Quorum owns this distribution's bootstrap and update
authority.
"""

from __future__ import annotations


EDITION_ID = "quorum"
PRODUCT_NAME = "Quorum"
PRODUCT_SLUG = "quorum"
HOME_DIR_NAME = ".quorum"
WINDOWS_HOME_DIR_NAME = "quorum"

SOURCE_REPOSITORY = "Quorum-Agent/hermes-agent"
SOURCE_REPO_URL = f"https://github.com/{SOURCE_REPOSITORY}.git"
SOURCE_REPO_SSH_URL = f"git@github.com:{SOURCE_REPOSITORY}.git"
SOURCE_REPO_CANONICAL = f"github.com/{SOURCE_REPOSITORY.lower()}"
RAW_SOURCE_BASE_URL = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}"
RELEASE_URL_BASE = f"https://github.com/{SOURCE_REPOSITORY}/releases/tag"

UPSTREAM_REPOSITORY = "NousResearch/hermes-agent"
UPSTREAM_REPO_URL = f"https://github.com/{UPSTREAM_REPOSITORY}.git"
UPSTREAM_ATTRIBUTION = "Based on Hermes Agent by Nous Research"

# A constant is convenient for guarded hot paths; the function gives callers a
# semantic API that can evolve without turning product detection into an env
# flag that an installed build could accidentally lose.
IS_QUORUM_EDITION = True


def is_quorum_edition() -> bool:
    """Return whether this source tree is the Quorum distribution."""

    return IS_QUORUM_EDITION
