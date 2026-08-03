"""Quorum's general Hermes plugin surface.

The Quorum Edition dispatch guard is host-owned and deliberately does not live
in this plugin: disabling a presentation plugin must never disable policy
enforcement.  This module contributes only the ``/quorum`` inspection command.
When installed into stock Hermes as part of Quorum Companion it reports that
the host guard is unavailable instead of implying fail-closed protection.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping


def _runtime_status() -> Mapping[str, Any]:
    try:
        dispatch = import_module("agent.quorum_dispatch")
        get_status = getattr(dispatch, "get_status")
        status = get_status()
    except (AttributeError, ImportError, RuntimeError) as exc:
        return {
            "available": False,
            "reason": type(exc).__name__,
        }

    if not isinstance(status, Mapping):
        return {
            "available": False,
            "reason": "invalid_status",
        }

    return status


def _format_status(status: Mapping[str, Any]) -> str:
    if status.get("available") is False:
        return (
            "Quorum Companion is installed, but this stock Hermes runtime does "
            "not expose the Quorum Edition dispatch guard. Companion features "
            "are best-effort visibility and do not provide fail-closed "
            "enforcement."
        )

    policy = status.get("default_policy") or status.get("policy") or "private"
    calls = status.get("event_count")
    calls_suffix = f"\nProcess-local decisions observed: {calls}" if isinstance(calls, int) else ""
    return (
        "Quorum Edition dispatch guard: available\n"
        f"Default policy: {policy}\n"
        "Inspection events are process-local and are not a durable audit ledger."
        f"{calls_suffix}"
    )


def _handle_quorum(raw_args: str) -> str:
    command = raw_args.strip().lower()
    if command not in {"", "status"}:
        return "Usage: /quorum [status]"
    return _format_status(_runtime_status())


def register(ctx) -> None:
    """Register the inspection command through Hermes' real plugin API."""

    ctx.register_command(
        "quorum",
        _handle_quorum,
        description="Show Quorum routing status and inspection semantics.",
        args_hint="[status]",
    )
