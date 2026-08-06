"""Salience producer — the Stage-2 watch-only observer.

This first-party observer plugs into the host's existing lifecycle dispatch and
turns real agent activity (tool calls, API errors, approvals) into **bounded
salience signals** recorded on a per-session audit bus, plus one **directive per
turn** (the judgment system's recorded decision for that turn). It is the
"produce" half of wiring SalienceOS into quorum-agent as a TEST RIG.

Hard guarantees, by construction:

* **Produce-only / no decision-path change.** Nothing here feeds back into what the
  agent does. Signals and a directive are *recorded*; consuming the directive (the
  compute-budget knob) is a separate, later change. The dispatch site wraps every
  call in ``_safe_observe`` (see ``observability/__init__.py``), and this module
  additionally swallows its own errors — a broken observer goes dark, it never
  breaks the turn.
* **Fail-closed attribution.** A signal is recorded only against an *open window
  with a matching turn id*. No resolvable ``session_id``/``turn_id`` ⇒ no window,
  no signal. Activity that can't be correlated to a turn is dropped, never guessed.
* **Audit fence (Finding G).** Only bounded, ref-shaped tokens reach the bus — the
  ``salienceos`` signal/directive validators reject anything prompt-sized. We never
  put tool args, results, or prose on the bus; only the *fact* of what happened.
* **Hashed session identity (ADR 0002 / A11).** The per-session bus file and the
  durable subject carry a one-way hash of ``session_id``, never the raw id, so the
  durable record can't be trivially re-linked to the ephemeral quorum_dispatch feed
  it complements.
* **Single-threaded bus contract.** ``SalienceBus`` is single-threaded by contract;
  all bus and window-registry access is serialized under ``_LOCK``.

Gating: active only when this is the Quorum Edition *and* config ``salience.enabled``
is not turned off (**default ON**, kill switch ``salience.enabled: false``). When
off — or if the vendored package is unavailable — ``handles_hook`` returns ``False``
for every hook, so the emitters (which gate on ``has_hook``) stay on their existing
zero-cost path and this observer is completely inert.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# The vendored judgment system. If it is somehow unavailable, the observer stays
# permanently dark rather than breaking the host's observability dispatch.
try:
    from salienceos.interpreter import Facet, SalienceSignal, interpret, issue_policy
    from salienceos.interpreter.bus import SalienceBus

    _IMPORT_OK = True
except Exception:  # pragma: no cover - defensive; a vendoring error must not crash the host
    _IMPORT_OK = False
    logger.warning("salience observer: vendored salienceos unavailable; staying dark", exc_info=True)

# --- what we watch -----------------------------------------------------------

# Window-open signal carries the full (session_id, task_id, turn_id) triple;
# post_tool_call / api_request_error are the produce events; the session-lifecycle
# events flush the final open window. pre_verify is deliberately absent (plugin-only
# hook; not part of the produce map).
SALIENCE_HANDLED_HOOKS = frozenset({
    "pre_llm_call",
    "post_tool_call",
    "api_request_error",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
})

SUBSYSTEM_ID = "quorum.observer"

# v0 signal map (Part 4 #4 / plan): approvals -> RISK, tool/api errors ->
# VERIFICATION, file mutations -> MEMORY. ADAPTATION is never produced (the
# inhibitor path is product-dormant under allow_adaptation=False — ADR 0002).
# These name heuristics are deliberately coarse for v0 and are the one place a
# real tool taxonomy will refine later; being wrong here only mis-*files* a signal,
# it can never grant anything (P-01).
_APPROVAL_MARKERS = ("approv", "permission", "consent", "escalat", "authoriz")
_MUTATION_MARKERS = ("write", "edit", "create_file", "apply_patch", "str_replace",
                     "delete", "remove", "move", "rename", "mkdir", "patch")

MAX_TOKEN_LEN = 128  # mirrors salienceos.interpreter.signal.MAX_TOKEN_LEN

# Process-local policy key. Policies are issued and interpreted entirely in-process
# and the bus stores unsigned directive payloads, so this is a transient HMAC secret
# for the issue/interpret round-trip, NOT a durable trust anchor (ADR 0002 claims no
# cross-process authenticity). Regenerated each process — honest about its scope.
_POLICY_KEY = os.urandom(32)

# Fallback compute budget baked into the produce policy. In v0 the directive's
# compute_budget is inert (nothing consumes it yet); PR-H2 binds this to the
# operator's resolved max_iterations (A4) when it wires the consumer.
_DEFAULT_BUDGET = 25

_LOCK = threading.Lock()


class _Window:
    """One open turn. Signals recorded against it must match its turn id."""

    __slots__ = ("session_id", "turn_id", "task_id", "subject", "signals", "closed")

    def __init__(self, session_id: str, turn_id: str, task_id: str, subject: str) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.task_id = task_id
        self.subject = subject
        self.signals: list[Any] = []
        self.closed = False


_WINDOWS: dict[str, _Window] = {}   # session_id -> current open window
_BUSES: dict[str, Any] = {}         # session_id -> SalienceBus


# --- gating ------------------------------------------------------------------

def _config_flag(key: str, default: bool) -> bool:
    """Read a boolean under the top-level ``salience`` config block. Missing key ⇒
    ``default``; explicit ``false`` ⇒ off; unreadable config ⇒ fail-closed (off)."""
    try:
        from hermes_cli.config import read_raw_config_readonly

        cfg = read_raw_config_readonly() or {}
    except Exception:
        return False
    salience = cfg.get("salience") if isinstance(cfg, dict) else None
    if not isinstance(salience, dict) or key not in salience:
        return default
    return salience.get(key) is not False


def salience_enabled() -> bool:
    """Quorum Edition AND not explicitly disabled (default ON)."""
    if not _IMPORT_OK:
        return False
    try:
        from product_identity import IS_QUORUM_EDITION
    except Exception:
        return False
    if not IS_QUORUM_EDITION:
        return False
    return _config_flag("enabled", True)


def handles_hook(hook_name: str) -> bool:
    return hook_name in SALIENCE_HANDLED_HOOKS and salience_enabled()


def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    """Dispatch one lifecycle event into the produce path. Never raises."""
    if not handles_hook(hook_name):
        return
    try:
        if hook_name == "pre_llm_call":
            _open_window(kwargs)
        elif hook_name == "post_tool_call":
            _record(kwargs, _map_tool_call)
        elif hook_name == "api_request_error":
            _record(kwargs, _map_api_error)
        elif hook_name in ("on_session_end", "on_session_finalize", "on_session_reset"):
            _close_session(kwargs)
    except Exception:  # pragma: no cover - belt: dispatch site already isolates us
        logger.warning("salience observer hook failed: %s", hook_name, exc_info=True)


# --- identity helpers --------------------------------------------------------

def _ids(kwargs: dict) -> tuple[str, str]:
    return str(kwargs.get("session_id") or ""), str(kwargs.get("turn_id") or "")


def _session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _subject(session_id: str, turn_id: str) -> str:
    """Durable arbitration key: hashed session component + turn id, bounded to a
    ref token. Same value for the window's signals and its policy (interpret needs
    them to share a subject)."""
    return (_session_hash(session_id)[:16] + ":" + turn_id)[:MAX_TOKEN_LEN]


# --- window lifecycle --------------------------------------------------------

def _open_window(kwargs: dict) -> None:
    session_id, turn_id = _ids(kwargs)
    if not session_id or not turn_id:
        return  # fail-closed attribution: no correlation id ⇒ no window
    task_id = str(kwargs.get("task_id") or "")
    with _LOCK:
        current = _WINDOWS.get(session_id)
        # A new turn finalizes the previous one first, so turn N truly governs
        # turn N+1 (A3): its directive is emitted before N+1 accumulates.
        if current is not None and not current.closed and current.turn_id != turn_id:
            _close_locked(current)
        if current is None or current.closed or current.turn_id != turn_id:
            _WINDOWS[session_id] = _Window(
                session_id, turn_id, task_id, _subject(session_id, turn_id)
            )


def _record(kwargs: dict, mapper) -> None:
    session_id, turn_id = _ids(kwargs)
    if not session_id or not turn_id:
        return
    with _LOCK:
        window = _WINDOWS.get(session_id)
        if window is None or window.closed or window.turn_id != turn_id:
            return  # no matching open window ⇒ drop (fail-closed)
        for signal in mapper(kwargs, window.subject):
            try:
                self_bus = _bus_for(session_id)
                self_bus.publish(signal)
                window.signals.append(signal)
            except Exception:
                logger.warning("salience observer: publish failed", exc_info=True)


def _close_session(kwargs: dict) -> None:
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return
    with _LOCK:
        window = _WINDOWS.get(session_id)
        if window is not None and not window.closed:
            _close_locked(window)


def _close_locked(window: _Window) -> None:
    """Finalize a turn: interpret its accumulated signals against the produce
    policy and emit the resulting directive to the bus. Idempotent."""
    if window.closed:
        return
    window.closed = True
    try:
        policy = issue_policy(
            "salience.observer.v0",   # policy_id
            window.subject,           # subject (matches the signals)
            (),                       # granted_capabilities: NONE (P-01)
            _operator_budget(),       # min_budget (A4: operator floor)
            _operator_budget(),       # max_budget (v0: pinned; PR-H2 widens)
            0,                        # min_verification
            3,                        # max_verification (FULL ceiling/default)
            "semantic",               # max_retention salience may buy
            False,                    # allow_adaptation: OFF (inhibitor dormant)
            2,                        # adaptation_min_verification (moot when off)
            0.5,                      # adaptation_max_risk (moot when off)
            False,                    # allow_immediate_reconfigure: between-turn only
            _POLICY_KEY,
        )
        directive = interpret(policy, tuple(window.signals), _POLICY_KEY)
        _bus_for(window.session_id).emit(directive)
    except Exception:
        logger.warning("salience observer: window finalize failed", exc_info=True)


# --- bus ---------------------------------------------------------------------

def _bus_for(session_id: str):
    """Lazily open the per-session bus (replays + verifies an existing JSONL on
    open — survives resume/restart). One file per session, named by the *hash* of
    the session id, under the host home."""
    bus = _BUSES.get(session_id)
    if bus is None:
        from pathlib import Path

        from hermes_constants import get_hermes_home

        directory = Path(get_hermes_home()) / "salience"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (_session_hash(session_id) + ".jsonl")
        bus = SalienceBus(str(path))
        _BUSES[session_id] = bus
    return bus


def _operator_budget() -> int:
    """The operator's resolved compute budget, used as the policy's min_budget
    floor (A4). Best-effort read of the configured iteration budget via the
    programmatic read path (NOT get_config_value — that is a CLI helper that
    sys.exit()s on a missing key); falls back to a safe constant. PR-H2 owns
    getting this exactly right (it consumes it)."""
    try:
        from hermes_cli.config import read_raw_config_readonly

        cfg = read_raw_config_readonly() or {}
    except Exception:
        return _DEFAULT_BUDGET
    for path in (("agent", "max_iterations"), ("max_iterations",), ("agent", "iteration_budget")):
        node: Any = cfg
        for part in path:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                node = None
                break
        if isinstance(node, int) and not isinstance(node, bool) and node > 0:
            return node
    return _DEFAULT_BUDGET


# --- signal mapping (bounded, ref-shaped) ------------------------------------

def _ref(*parts: str) -> tuple:
    """Bounded, non-empty ref tokens for provenance (audit fence)."""
    out = []
    for part in parts:
        token = str(part)[:MAX_TOKEN_LEN]
        if token:
            out.append(token)
    return tuple(out[:16])


def _signal(subject: str, facet: str, influence: float, provenance: tuple):
    return SalienceSignal(SUBSYSTEM_ID, subject, facet, influence, 1.0, provenance)


def _map_tool_call(kwargs: dict, subject: str) -> list:
    tool_name = str(kwargs.get("tool_name") or "")
    status = str(kwargs.get("status") or "")
    error_type = str(kwargs.get("error_type") or "")
    is_error = bool(error_type) or status.lower() in ("error", "failed", "failure")
    lowered = tool_name.lower()
    provenance = _ref("tool:" + tool_name, "status:" + status)
    if is_error:
        # something did not do what it claimed -> wants verification
        return [_signal(subject, Facet.VERIFICATION, 0.7, provenance)]
    if any(marker in lowered for marker in _APPROVAL_MARKERS):
        return [_signal(subject, Facet.RISK, 0.6, provenance)]
    if any(marker in lowered for marker in _MUTATION_MARKERS):
        return [_signal(subject, Facet.MEMORY, 0.4, provenance)]
    return []


def _map_api_error(kwargs: dict, subject: str) -> list:
    # A non-retryable API failure is more salient than a transient one.
    retryable = kwargs.get("retryable")
    influence = 0.5 if retryable is True else 0.8
    provenance = _ref("api_error", "provider:" + str(kwargs.get("provider") or ""))
    return [_signal(subject, Facet.VERIFICATION, influence, provenance)]


def _reset_for_tests() -> None:
    """Drop all in-memory windows/buses. Test-only; never called in production."""
    with _LOCK:
        _WINDOWS.clear()
        _BUSES.clear()
