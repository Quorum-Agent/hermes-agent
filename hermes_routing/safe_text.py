"""safeDisplayText — Python port of packages/core/src/safe-text.ts.

The TS source applies a single regex `[\u0000-\u001f\u007f-\u009f]|Cf-format`
replacing every match with a SPACE, then collapses whitespace and trims. That
replacement (not removal) is load-bearing for the expected output — a control
between two words must become a single separating space after collapse. We
detect the characters via unicodedata category 'Cf' plus explicit C0/C1 ranges,
matching the TS character class rather than maintaining a hand-rolled list.
"""

from __future__ import annotations

import re
import unicodedata


def _is_cf_or_control(ch: str) -> bool:
    code = ord(ch)
    if 0x00 <= code <= 0x1F or 0x7F <= code <= 0x9F:
        return True
    return unicodedata.category(ch) == "Cf"


def _replace(value: str) -> str:
    parts = []
    for ch in value:
        parts.append(" " if _is_cf_or_control(ch) else ch)
    return "".join(parts)


def safe_display_text(value: str, maximum_length: int = 240) -> str:
    """Make arbitrary text safe to place in the interface.

    Normalizes to NFKC, replaces C0/C1 controls and every Unicode format
    character with a space, collapses whitespace, and bounds the length.
    """
    normalized = unicodedata.normalize("NFKC", value)
    scrubbed = _replace(normalized)
    collapsed = re.sub(r"\s+", " ", scrubbed).strip()
    return collapsed[:maximum_length]
