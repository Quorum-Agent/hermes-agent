"""Route planner — Python port of packages/core/src/route-planner.ts.

Returns a full TaskPlan (not the spike's simplified PlanResult). Selection is:
availability/capability/ceiling first, then a data-sensitivity floor (equality,
not ceiling), then degrade-to-scaffold, then rank.
"""

from __future__ import annotations

from .types import (
    CompiledRequest,
    ExecutionAttempt,
    ModelDescriptor,
    PlanStep,
    TaskPlan,
    leaves_device,
    location_tier,
    model_reach,
)
from .policies import get_policy, require_enforceable_policy
from .uuid import random_uuid

SPECIALTY_BONUS = 18
LOCAL_BONUS = 100
FRESHNESS_BONUS = 20


def _physical_identity(model: ModelDescriptor) -> str:
    return f"{model.location}:{model.provider}:{model.label}"


def _supports(model: ModelDescriptor, request: CompiledRequest) -> bool:
    return all(c in model.capabilities for c in request.requirements.capabilities)


def _matched_specialties(model: ModelDescriptor, request: CompiledRequest) -> list[str]:
    seen = set()
    out = []
    for s in model.specialties or []:
        if s != "chat" and s in request.requirements.capabilities and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _specialty_score(model: ModelDescriptor, request: CompiledRequest) -> int:
    return len(_matched_specialties(model, request)) * SPECIALTY_BONUS


def _local_score(model: ModelDescriptor, request: CompiledRequest) -> int:
    score = LOCAL_BONUS if model.location == "local" else 0
    score += model.quality_rating
    score += _specialty_score(model, request)
    if request.requirements.requires_freshness and "web" in model.capabilities:
        score += FRESHNESS_BONUS
    return score


def _plan_reach(steps: list[PlanStep]) -> str:
    """The furthest tier any step of the plan reaches.

    Only stages that receive conversation content (model/synthesis) count;
    retrieval is excluded because it egresses a query, disclosed separately.
    """
    reach = "local"
    for step in steps:
        if step.kind not in ("model", "synthesis"):
            continue
        if step.location == "device":
            continue
        if location_tier(step.location) > location_tier(reach):
            reach = step.location
    return reach


class RoutePlanner:
    def __init__(self, mode: str = "route"):
        self._mode = mode  # route|relay

    def _select_hub(self, candidates: list[ModelDescriptor], spoke: ModelDescriptor):
        if self._mode != "relay":
            return None
        spoke_identity = _physical_identity(spoke)
        for model in candidates:
            if (
                model.role == "general"
                and model.location == "local"
                and _physical_identity(model) != spoke_identity
            ):
                return model
        return None

    def plan(
        self,
        request: CompiledRequest,
        models: list[ModelDescriptor],
        excluded_model_ids: frozenset[str] | None = None,
    ) -> TaskPlan:
        excluded = excluded_model_ids or frozenset()
        require_enforceable_policy(request.policy)
        policy = get_policy(request.policy)

        eligible = [
            m
            for m in models
            if m.id not in excluded
            and m.available
            and _supports(m, request)
            and location_tier(model_reach(m)) <= location_tier(policy.inference_ceiling)
        ]

        requires_local = (
            request.requirements.contains_sensitive_data
            or request.requirements.contains_web_grounded_data
        )
        candidates = (
            [m for m in eligible if m.location == "local"]
            if requires_local
            else eligible
        )

        degraded = False
        if not candidates:
            candidates = [
                m
                for m in models
                if m.id not in excluded
                and m.available
                and m.location == "local"
                and m.transport == "in_process"
                and m.provider == "quorum"
                and "chat" in m.capabilities
            ]
            degraded = bool(candidates)
        if not candidates:
            raise ValueError(
                f"No available model satisfies the {policy.label} policy and required capabilities."
            )

        if policy.prefer_local:
            candidates.sort(key=lambda m: _local_score(m, request), reverse=True)
        else:
            candidates.sort(
                key=lambda m: (
                    m.quality_rating + _specialty_score(m, request),
                    m.context_window,
                ),
                reverse=True,
            )
        selected = candidates[0]
        degraded = degraded or selected.id == "local:scaffold"

        hub = self._select_hub(candidates, selected)
        matched = _matched_specialties(selected, request)

        if degraded:
            base_rationale = (
                f"{policy.label} mode found no model with every required capability; "
                "the local scaffold will explain the limitation."
            )
        elif not leaves_device(selected.location):
            base_rationale = (
                f"{policy.label} mode selected a local {' and '.join(matched)} specialist."
                if matched
                else f"{policy.label} mode selected an available local model with the required capabilities."
            )
        else:
            base_rationale = (
                f"{policy.label} mode selected a {selected.location} model because it "
                "best matches the request requirements."
            )

        steps: list[PlanStep] = [
            PlanStep(
                id=random_uuid(),
                label="Compile request context",
                kind="compile",
                location="device",
            ),
            PlanStep(
                id=random_uuid(),
                label=f"Apply {policy.label.lower()} policy",
                kind="policy",
                location="device",
            ),
        ]
        model_label = (
            f"Draft with {selected.label}" if hub else f"Generate with {selected.label}"
        )
        steps.append(
            PlanStep(
                id=random_uuid(),
                label=model_label,
                kind="model",
                location=selected.location,
                model_id=selected.id,
            )
        )
        if hub:
            steps.append(
                PlanStep(
                    id=random_uuid(),
                    label=f"Synthesize with {hub.label}",
                    kind="synthesis",
                    location=hub.location,
                    model_id=hub.id,
                )
            )

        route = _plan_reach(steps)
        model_id = hub.id if hub else selected.id

        if hub:
            rationale = (
                f"{base_rationale} {hub.label} will synthesize the final answer."
            )
        elif self._mode == "relay":
            rationale = (
                f"{base_rationale} No second general model could serve this request, "
                "so one model answered directly."
            )
        else:
            rationale = base_rationale

        return TaskPlan(
            id=random_uuid(),
            request_id=request.id,
            policy=request.policy,
            verbosity=request.verbosity,
            analysis=request.analysis,
            route=route,
            model_id=model_id,
            spoke_model_id=selected.id if hub else None,
            synthesis_degraded=True if (self._mode == "relay" and not hub) else None,
            rationale=rationale,
            steps=steps,
            degraded=True if degraded else None,
            attempts=[],
            cloud_disclosure=(
                "The conversation context required by the selected model will leave this device."
                if leaves_device(route)
                else None
            ),
            safety={
                "sensitive_data_categories": request.requirements.sensitive_data_categories,
                "contains_web_grounded_data": request.requirements.contains_web_grounded_data,
            },
        )
