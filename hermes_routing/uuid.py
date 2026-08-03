"""randomUUID — Python port of packages/core/src/uuid.ts."""

from __future__ import annotations

import uuid


def random_uuid() -> str:
    return str(uuid.uuid4())
