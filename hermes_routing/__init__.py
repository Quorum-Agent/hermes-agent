"""hermes_routing — Quorum's policy core, ported to Python.

Zero-dependency package. The executable specification for the policy layer:
compiler (intent, capabilities, sensitivity), planner (ceiling-constrained
route), orchestrator (plan/trace/delta/result/error events), and envelope
(validated model output).

This is a separable package; it imports only the Python standard library. It
touches no Hermes internals.
"""

from .compiler import RequestCompiler, detect_sensitive_content
from .envelope import UnsafeOutput, extract_public_answer, strip_reasoning_fields
from .orchestrator import ModelExecutionError, Orchestrator
from .planner import RoutePlanner
from .policies import (
    POLICIES,
    UnsupportedPolicyError,
    get_policy,
    require_enforceable_policy,
)
from .policy_copy import (
    LOCATION_NOUNS,
    policies_without_search,
    policy_description,
)
from .safe_text import safe_display_text
from .types import (
    EXECUTION_LOCATIONS,
    WEB_SEARCH_LOCATION,
    leaves_device,
    location_tier,
    model_reach,
    policy_permits_tool,
    policy_reaches_off_device,
)
from .uuid import random_uuid

__all__ = [
    "EXECUTION_LOCATIONS",
    "LOCATION_NOUNS",
    "ModelExecutionError",
    "Orchestrator",
    "POLICIES",
    "WEB_SEARCH_LOCATION",
    "RoutePlanner",
    "RequestCompiler",
    "UnsupportedPolicyError",
    "UnsafeOutput",
    "detect_sensitive_content",
    "extract_public_answer",
    "get_policy",
    "leaves_device",
    "location_tier",
    "model_reach",
    "policies_without_search",
    "policy_description",
    "policy_permits_tool",
    "policy_reaches_off_device",
    "random_uuid",
    "require_enforceable_policy",
    "safe_display_text",
    "strip_reasoning_fields",
]
