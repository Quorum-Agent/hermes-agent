"""First-party Hermes observability integrations."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    """Dispatch a Hermes lifecycle event to built-in observability features."""
    from . import relay_shared_metrics

    _safe_observe(relay_shared_metrics.observe_lifecycle, hook_name, kwargs)
    # Import the salience observer independently so a failure to import it can
    # never starve the relay dispatch above (which must always run first).
    try:
        from . import salience_observer
    except Exception:
        logger.warning("Salience observer unavailable", exc_info=True)
        return
    _safe_observe(salience_observer.observe_lifecycle, hook_name, kwargs)


def handles_hook(hook_name: str) -> bool:
    """Return whether any built-in observability feature handles a hook."""
    from . import relay_shared_metrics

    if relay_shared_metrics.handles_hook(hook_name):
        return True
    # Independent import for the same reason: a salience import failure must not
    # mask the relay answer computed above.
    try:
        from . import salience_observer
    except Exception:
        return False
    return salience_observer.handles_hook(hook_name)


def _safe_observe(callback: Any, hook_name: str, kwargs: dict[str, Any]) -> None:
    try:
        callback(hook_name, **kwargs)
    except Exception:
        logger.warning(
            "Built-in observability hook failed: %s", hook_name, exc_info=True
        )
