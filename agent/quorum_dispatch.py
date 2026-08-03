"""Mandatory Quorum Edition policy checks at physical dispatch boundaries.

The desktop plugin is a control and inspection surface.  This module is the
security boundary: Quorum Edition calls it after mutable middleware and again
immediately before a provider or network-capable tool is invoked.  It has no
dependency on plugin discovery or enablement.

The in-memory event buffer is deliberately an inspector feed, not an audit
ledger.  Requests, prompts, tool arguments, and credentials are never retained.
"""

from __future__ import annotations

import hashlib
import ipaddress
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Mapping
from urllib.parse import urlparse


POLICY_MODES = frozenset(
    {"private", "balanced", "quality", "offline", "cost_controlled"}
)
LOCATION_TIERS = {"device": 0, "local": 1, "network": 2, "cloud": 3}
POLICY_CEILINGS = {
    "offline": "device",
    "private": "local",
    "balanced": "cloud",
    "quality": "cloud",
}

_DEVICE_PROVIDERS = frozenset({"quorum-scaffold", "in-process", "mock"})
_CLOUD_PROVIDER_HINTS = frozenset(
    {
        "anthropic",
        "bedrock",
        "cerebras",
        "codex",
        "copilot",
        "deepseek",
        "gemini",
        "google",
        "groq",
        "kimi",
        "minimax",
        "moonshot",
        "nous",
        "nvidia",
        "openai",
        "openrouter",
        "perplexity",
        "together",
        "xai",
    }
)
_LOCAL_TOOL_NAMES = frozenset(
    {
        "clarify",
        "close_terminal",
        "cronjob",
        "delegate_task",
        "execute_code",
        "file",
        "focus_pane",
        "memory",
        "patch",
        "process",
        "project_create",
        "project_list",
        "project_switch",
        "read_file",
        "read_terminal",
        "search_files",
        "session_search",
        "skill_manage",
        "skill_view",
        "skills_list",
        "terminal",
        "todo",
        "todo_read",
        "todo_write",
        "write_file",
    }
)
_NETWORK_TOOL_PREFIXES = (
    "browser",
    "email",
    "exa",
    "firecrawl",
    "image_gen",
    "mcp_",
    "send_message",
    "slack",
    "teams",
    "telegram",
    "tts",
    "video_gen",
    "web_",
)


class QuorumPolicyError(RuntimeError):
    """Base class for fail-closed Quorum dispatch failures."""


class QuorumPolicyViolation(QuorumPolicyError):
    """Raised when the effective dispatch exceeds the active policy."""


class QuorumPolicyUnavailable(QuorumPolicyError):
    """Raised when the mandatory policy engine cannot make a decision."""


@dataclass(frozen=True)
class DispatchSettings:
    default_policy: str = "private"
    cloud_consent: bool = False
    session_policies: Mapping[str, str] | None = None

    def policy_for(self, session_id: str) -> str:
        overrides = self.session_policies or {}
        policy = str(overrides.get(session_id) or self.default_policy).strip().lower()
        if policy not in POLICY_MODES:
            raise QuorumPolicyUnavailable(f"Unknown Quorum policy mode: {policy!r}")
        return policy


@dataclass(frozen=True)
class DispatchDecision:
    allowed: bool
    policy: str
    reach: str
    reason: str
    provider: str = ""
    model: str = ""
    session_id: str = ""
    call_role: str = "primary"
    sensitive_categories: tuple[str, ...] = ()


_EVENTS: deque[dict[str, Any]] = deque(maxlen=500)
_EVENT_LOCK = threading.Lock()
_EVENT_SEQUENCE = 0


def _edition_enabled() -> bool:
    try:
        from product_identity import IS_QUORUM_EDITION
    except Exception as exc:  # pragma: no cover - packaging failure path
        raise QuorumPolicyUnavailable(
            "Quorum product identity is unavailable; provider dispatch is blocked"
        ) from exc
    return bool(IS_QUORUM_EDITION)


def _load_settings() -> DispatchSettings:
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly()
    except Exception as exc:
        raise QuorumPolicyUnavailable(
            "Quorum could not read config.yaml; provider dispatch is blocked"
        ) from exc
    raw = config.get("quorum", {}) if isinstance(config, dict) else {}
    if not isinstance(raw, dict):
        raise QuorumPolicyUnavailable("The quorum config section must be a mapping")
    session_policies = raw.get("session_policies", {})
    if not isinstance(session_policies, dict):
        raise QuorumPolicyUnavailable("quorum.session_policies must be a mapping")
    return DispatchSettings(
        default_policy=str(raw.get("default_policy") or "private"),
        cloud_consent=raw.get("cloud_consent") is True,
        session_policies={str(key): str(value) for key, value in session_policies.items()},
    )


def _host_reach(base_url: str) -> str | None:
    value = str(base_url or "").strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith(("acp://", "acp+tcp://")):
        return "cloud"
    candidate = value if "://" in value else f"http://{value}"
    try:
        host = (urlparse(candidate).hostname or "").strip().lower()
    except Exception:
        return None
    if not host:
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return "local"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            return "network"
        return "cloud"
    if address.is_loopback:
        return "local"
    if address.is_private or address.is_link_local:
        return "network"
    if isinstance(address, ipaddress.IPv4Address) and address in ipaddress.ip_network(
        "100.64.0.0/10"
    ):
        return "network"
    return "cloud"


def classify_model_reach(provider: str, base_url: str = "") -> str:
    """Classify the physical provider boundary conservatively.

    Loopback is a separate local process. RFC-1918/Tailscale/unqualified hosts
    are network peers. Unknown providers without an endpoint are treated as
    cloud so a missing descriptor can never loosen a policy.
    """

    normalized = str(provider or "").strip().lower()
    normalized_endpoint = str(base_url or "").strip().lower().rstrip("/")
    # MoA's outer facade is an in-process dispatcher, not a network endpoint.
    # Its reference and aggregator calls are independently guarded after they
    # resolve to their physical providers.  Match only the canonical virtual
    # identity so a user-supplied ``moa://remote`` endpoint cannot gain device
    # privileges.
    if normalized == "moa" and normalized_endpoint == "moa://local":
        return "device"

    endpoint_reach = _host_reach(base_url)
    if endpoint_reach is not None:
        return endpoint_reach
    if normalized in _DEVICE_PROVIDERS:
        return "device"
    if any(hint in normalized for hint in _CLOUD_PROVIDER_HINTS):
        return "cloud"
    return "cloud"


def _text_parts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, child in value.items():
            if str(key).lower() in {
                "api_key",
                "authorization",
                "extra_headers",
                "headers",
            }:
                continue
            parts.extend(_text_parts(child))
        return parts
    if isinstance(value, (list, tuple)):
        parts = []
        for child in value:
            parts.extend(_text_parts(child))
        return parts
    return []


def _sensitive_categories(request: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        from hermes_routing.compiler import detect_sensitive_content
    except Exception as exc:
        raise QuorumPolicyUnavailable(
            "Quorum's sensitive-data compiler is unavailable; dispatch is blocked"
        ) from exc

    categories: set[str] = set()
    for text in _text_parts(request):
        try:
            categories.update(str(category) for category in detect_sensitive_content(text))
        except Exception as exc:
            raise QuorumPolicyUnavailable(
                "Quorum could not classify the outbound request; dispatch is blocked"
            ) from exc
    return tuple(sorted(categories))


def evaluate_dispatch(
    request: Mapping[str, Any],
    *,
    provider: str,
    model: str,
    base_url: str = "",
    session_id: str = "",
    call_role: str = "primary",
    settings: DispatchSettings | None = None,
) -> DispatchDecision:
    """Return the decision for one effective provider request."""

    if not _edition_enabled():
        return DispatchDecision(
            allowed=True,
            policy="host",
            reach="unknown",
            reason="Quorum Edition enforcement is not active",
            provider=provider,
            model=model,
            session_id=session_id,
            call_role=call_role,
        )
    active = settings or _load_settings()
    policy = active.policy_for(session_id)
    reach = classify_model_reach(provider, base_url)
    categories = _sensitive_categories(request)

    if policy == "cost_controlled":
        return DispatchDecision(
            False,
            policy,
            reach,
            "Cost-controlled routing is unavailable until a usage ledger is configured",
            provider,
            model,
            session_id,
            call_role,
            categories,
        )

    ceiling = POLICY_CEILINGS[policy]
    if LOCATION_TIERS[reach] > LOCATION_TIERS[ceiling]:
        return DispatchDecision(
            False,
            policy,
            reach,
            f"{policy.title()} policy permits {ceiling}-tier inference, not {reach}",
            provider,
            model,
            session_id,
            call_role,
            categories,
        )
    if categories and LOCATION_TIERS[reach] > LOCATION_TIERS["local"]:
        return DispatchDecision(
            False,
            policy,
            reach,
            "Sensitive content may only be sent to device or loopback inference",
            provider,
            model,
            session_id,
            call_role,
            categories,
        )
    if LOCATION_TIERS[reach] > LOCATION_TIERS["local"] and not active.cloud_consent:
        return DispatchDecision(
            False,
            policy,
            reach,
            "Off-device inference requires explicit cloud consent",
            provider,
            model,
            session_id,
            call_role,
            categories,
        )
    return DispatchDecision(
        True,
        policy,
        reach,
        "Dispatch satisfies the active Quorum ceilings",
        provider,
        model,
        session_id,
        call_role,
        categories,
    )


def _record(kind: str, decision: DispatchDecision) -> None:
    global _EVENT_SEQUENCE
    event = {
        "kind": kind,
        "timestamp": time.time(),
        **asdict(decision),
    }
    # Defensive normalization: never let a future dataclass field smuggle a
    # request body into this process-memory inspector feed.
    event.pop("request", None)
    event.pop("args", None)
    session_id = str(event.get("session_id") or "")
    if session_id:
        event["session_id"] = f"sha256:{hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:12]}"
    with _EVENT_LOCK:
        _EVENT_SEQUENCE += 1
        event["id"] = _EVENT_SEQUENCE
        _EVENTS.append(event)


def enforce_model_request(
    request: Mapping[str, Any],
    *,
    provider: str,
    model: str,
    base_url: str = "",
    session_id: str = "",
    call_role: str = "primary",
) -> dict[str, Any]:
    """Fail closed unless one effective physical provider call is permitted."""

    try:
        decision = evaluate_dispatch(
            request,
            provider=provider,
            model=model,
            base_url=base_url,
            session_id=session_id,
            call_role=call_role,
        )
    except QuorumPolicyError as exc:
        decision = DispatchDecision(
            False,
            "unavailable",
            "unknown",
            str(exc),
            provider,
            model,
            session_id,
            call_role,
        )
        _record("model_dispatch", decision)
        raise
    _record("model_dispatch", decision)
    if not decision.allowed:
        raise QuorumPolicyViolation(decision.reason)
    return dict(request)


def classify_tool_reach(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().lower()
    if normalized in _LOCAL_TOOL_NAMES:
        return "local"
    if normalized.startswith(_NETWORK_TOOL_PREFIXES):
        return "cloud"
    # Unknown/plugin tools are not silently treated as local. Operators can
    # still use them in a consented Balanced/Quality session.
    return "cloud"


def enforce_tool_dispatch(
    tool_name: str,
    *,
    args: Mapping[str, Any] | None = None,
    session_id: str = "",
    call_role: str = "tool",
) -> None:
    if not _edition_enabled():
        return
    active = _load_settings()
    policy = active.policy_for(session_id)
    reach = classify_tool_reach(tool_name)
    categories = _sensitive_categories({"args": args or {}})
    allowed = True
    reason = "Tool satisfies the active Quorum ceilings"
    if policy == "cost_controlled":
        allowed = False
        reason = "Cost-controlled routing is unavailable until a usage ledger is configured"
    elif reach == "cloud" and categories:
        allowed = False
        reason = "Sensitive content may not be sent through a network-capable tool"
    elif reach == "cloud" and policy in {"private", "offline"}:
        allowed = False
        reason = f"{policy.title()} policy blocks network-capable tool {tool_name!r}"
    elif reach == "cloud" and not active.cloud_consent:
        allowed = False
        reason = "Network-capable tools require explicit cloud consent"
    decision = DispatchDecision(
        allowed,
        policy,
        reach,
        reason,
        provider="tool",
        model=tool_name,
        session_id=session_id,
        call_role=call_role,
        sensitive_categories=categories,
    )
    _record("tool_dispatch", decision)
    if not allowed:
        raise QuorumPolicyViolation(reason)


def list_events(limit: int = 100, before: int | None = None) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    with _EVENT_LOCK:
        events = list(_EVENTS)
    if before is not None:
        events = [event for event in events if int(event["id"]) < int(before)]
    return [dict(event) for event in reversed(events[-bounded:])]


def get_status() -> dict[str, Any]:
    try:
        settings = _load_settings()
        settings_error = ""
    except QuorumPolicyError as exc:
        settings = DispatchSettings()
        settings_error = str(exc)
    with _EVENT_LOCK:
        event_count = len(_EVENTS)
        last_event_id = _EVENT_SEQUENCE
    return {
        "edition": "quorum",
        "available": not bool(settings_error),
        "health": "active" if not settings_error else "degraded",
        "reason": "" if not settings_error else "config_unavailable",
        "mandatory": True,
        "fail_closed": True,
        "default_policy": settings.default_policy,
        "cloud_consent": settings.cloud_consent,
        "events_durable": False,
        "event_count": event_count,
        "last_event_id": last_event_id,
        "settings_error": settings_error,
    }


def _reset_for_tests() -> None:
    global _EVENT_SEQUENCE
    with _EVENT_LOCK:
        _EVENTS.clear()
        _EVENT_SEQUENCE = 0
