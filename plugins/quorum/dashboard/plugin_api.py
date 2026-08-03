"""Authenticated dashboard API for Quorum settings and inspection.

Mounted by Hermes at ``/api/plugins/quorum``.  Policy enforcement remains in
``agent.quorum_dispatch``; this module is a profile-aware control and read-only
inspection surface.  Events are intentionally process-memory snapshots, not a
durable audit ledger.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Literal, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from hermes_cli.config import load_config, save_config


router = APIRouter()

PolicyName = Literal["private", "balanced", "quality", "offline"]
_POLICIES = {"private", "balanced", "quality", "offline"}
_EVENT_FIELDS = (
    "id",
    "timestamp",
    "occurred_at",
    "kind",
    "session_id",
    "policy",
    "reach",
    "provider",
    "model",
    "call_role",
    "reason",
    "error",
)


class QuorumSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_policy: PolicyName
    cloud_consent: bool


def _load_dispatch_module():
    return import_module("agent.quorum_dispatch")


def _settings_from_config() -> dict[str, Any]:
    config = load_config() or {}
    raw = config.get("quorum") if isinstance(config, dict) else None
    quorum = raw if isinstance(raw, dict) else {}
    policy = quorum.get("default_policy", "private")
    if policy not in _POLICIES:
        policy = "private"
    session_policies = quorum.get("session_policies")
    return {
        "default_policy": policy,
        "cloud_consent": quorum.get("cloud_consent") is True,
        # Expose cardinality, not session identifiers. The inspector must not
        # turn the settings endpoint into a session-inventory leak.
        "session_override_count": len(session_policies) if isinstance(session_policies, dict) else 0,
    }


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _status_snapshot() -> dict[str, Any]:
    try:
        dispatch = _load_dispatch_module()
        status = dispatch.get_status()
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

    snapshot = {str(key): _json_scalar(value) for key, value in status.items()}
    snapshot.setdefault("available", not bool(snapshot.get("settings_error")))
    if snapshot.get("settings_error"):
        snapshot["available"] = False
        snapshot.setdefault("health", "degraded")
        snapshot.setdefault("reason", "config_unavailable")
    snapshot["events_durable"] = False
    return snapshot


def _project_event(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    event = {
        key: _json_scalar(raw[key])
        for key in _EVENT_FIELDS
        if key in raw
    }
    allowed = raw.get("allowed")
    if isinstance(allowed, bool):
        event["allowed"] = allowed
        event["decision"] = "allowed" if allowed else "blocked"
    categories = raw.get("sensitive_categories")
    if isinstance(categories, (list, tuple)):
        event["sensitive_categories"] = [
            str(category) for category in categories if isinstance(category, (int, str))
        ]
    return event or None


def _events_snapshot(*, limit: int, before: Optional[str]) -> dict[str, Any]:
    try:
        dispatch = _load_dispatch_module()
        kwargs: dict[str, Any] = {"limit": limit}
        if before is not None:
            kwargs["before"] = int(before) if before.isdecimal() else before
        raw = dispatch.list_events(**kwargs)
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "events": [],
            "available": False,
            "reason": type(exc).__name__,
            "durable": False,
        }

    next_before: Any = None
    if isinstance(raw, Mapping):
        items = raw.get("events", [])
        next_before = raw.get("next_before") or raw.get("cursor")
    else:
        items = raw
    if not isinstance(items, (list, tuple)):
        items = []

    events = [event for item in items if (event := _project_event(item)) is not None]
    return {
        "events": events[:limit],
        "available": True,
        "next_before": _json_scalar(next_before),
        "durable": False,
    }


@router.get("/settings")
def get_settings():
    return _settings_from_config()


@router.put("/settings")
def update_settings(payload: QuorumSettingsBody):
    try:
        config = load_config() or {}
        quorum = config.setdefault("quorum", {})
        if not isinstance(quorum, dict):
            quorum = {}
            config["quorum"] = quorum
        quorum["default_policy"] = payload.default_policy
        quorum["cloud_consent"] = payload.cloud_consent
        save_config(
            config,
            preserve_keys={
                ("quorum", "default_policy"),
                ("quorum", "cloud_consent"),
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to save Quorum settings: {exc}") from exc

    return _settings_from_config()


@router.get("/status")
def get_status():
    return _status_snapshot()


@router.get("/events")
def get_events(
    limit: int = Query(50, ge=1, le=200),
    before: Optional[str] = Query(None, min_length=1, max_length=256),
):
    return _events_snapshot(limit=limit, before=before)


@router.get("/overview")
def get_overview(limit: int = Query(50, ge=1, le=200)):
    return {
        "settings": _settings_from_config(),
        "status": _status_snapshot(),
        "inspection": _events_snapshot(limit=limit, before=None),
        "enforcement_controlled_by_host": True,
    }
