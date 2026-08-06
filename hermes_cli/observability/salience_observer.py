"""Salience producer — the Stage-2 watch-only observer.

This first-party observer plugs into the host's existing lifecycle dispatch and
turns real agent activity (tool calls, API errors, approvals) into **bounded
salience signals** recorded on a per-session audit bus, plus one **directive per
turn** (the judgment system's recorded decision for that turn). It is the
"produce" half of wiring SalienceOS into quorum-agent as a TEST RIG.

Hard guarantees, by construction:

* **Produce-only observer / no decision-path change.** The *observer* half
  (``observe_lifecycle`` and everything it drives) never feeds back into what the
  agent does: signals and a directive are only *recorded*. The dispatch site wraps
  every call in ``_safe_observe`` (see ``observability/__init__.py``), and this
  module additionally swallows its own errors — a broken observer goes dark, it
  never breaks the turn. The one *consumer* — ``bounded_iterations`` (PR-H2, the
  compute-budget knob) — is a separate, explicitly-gated entry point with its own
  kill switch (``salience.consume_compute``); it applies the recorded decision and
  fails open to the caller's value. It is wired live but INERT in v0: the produce
  policy pins ``min_budget == max_budget == operator budget`` and ATTENTION is
  unmapped, so the directive always echoes the operator's own budget — the consumer
  is behavior-preserving by construction. Moving the budget requires a future,
  separately-reviewed change that BOTH maps a budget-moving facet AND widens the
  policy window (``max_budget > min_budget``); a facet mapping alone, against a
  pinned window, cannot move it.
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

import copy
import hashlib
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# The vendored judgment system. If it is somehow unavailable, the observer stays
# permanently dark rather than breaking the host's observability dispatch.
try:
    from salienceos.interpreter import (
        Facet,
        SalienceSignal,
        interpret,
        issue_policy,
        verify_policy,
    )
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

# Fallback compute budget for the produce policy when no operator max_iterations is
# configured. It flows into the policy's min/max floor (_operator_budget → A4) and
# thus into the directive.compute_budget the consumer now reads; in v0 that value
# echoes the operator budget, so the consumer stays behavior-preserving.
_DEFAULT_BUDGET = 25

# Operator budget resolved once per process by _operator_budget() (only ever
# called under _LOCK); None until first resolved, cleared by _reset_for_tests.
_OPERATOR_BUDGET_CACHE = None

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

# session_id -> last CLOSED window's Directive. This is what the compute-budget
# consumer reads (turn N applies turn N-1's recorded decision). Written by
# _close_locked, replaced on turn rollover, and freed on session close alongside
# _WINDOWS/_BUSES — leaving it would reintroduce the per-session leak _close_session
# exists to prevent. Empty after a restart; the consumer then recovers from disk.
_LAST_DIRECTIVE: dict[str, Any] = {}

# One-time result of validating the v0 policy template through verify_policy
# (None = not yet checked). A rejected template can never brick the agent (the
# consumer's deny-shaped guard falls back to the operator default), but it must be
# surfaced loudly so a bad config is diagnosable rather than silently inert.
_TEMPLATE_VALIDATED = None


# --- gating ------------------------------------------------------------------

_OFF_VALUES = frozenset({"false", "0", "no", "off", "n", ""})


def _looks_off(value) -> bool:
    """A kill switch must honor any clearly-off value, not only bool ``False``: an
    operator who writes ``enabled: "false"`` / ``0`` / ``off`` means OFF. Anything
    not recognizably off is treated as on (the block/key was present on purpose)."""
    if value is False or value is None:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in _OFF_VALUES
    return False


def _config_flag(key: str, default: bool) -> bool:
    """Read a boolean under the top-level ``salience`` config block. Missing key ⇒
    ``default``; a recognizably-off value ⇒ off; unreadable config ⇒ fail-closed."""
    try:
        from hermes_cli.config import read_raw_config_readonly

        cfg = read_raw_config_readonly() or {}
    except (Exception, SystemExit):  # a config helper that sys.exit()s must not crash the host
        return False
    salience = cfg.get("salience") if isinstance(cfg, dict) else None
    if not isinstance(salience, dict) or key not in salience:
        return default
    return not _looks_off(salience.get(key))


def salience_enabled() -> bool:
    """Quorum Edition AND not explicitly disabled (default ON)."""
    if not _IMPORT_OK:
        return False
    # (Exception, SystemExit): the gate runs on the tool-call hot path via has_hook,
    # OUTSIDE observe_lifecycle's guard — a host API here that sys.exit()s must not
    # crash the host. KeyboardInterrupt is intentionally NOT caught anywhere.
    try:
        from product_identity import IS_QUORUM_EDITION
    except (Exception, SystemExit):
        return False
    if not IS_QUORUM_EDITION:
        return False
    return _config_flag("enabled", True)


def handles_hook(hook_name: str) -> bool:
    return hook_name in SALIENCE_HANDLED_HOOKS and salience_enabled()


def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    """Dispatch one lifecycle event into the produce path.

    Never propagates: catches Exception AND SystemExit — a host API the observer
    calls could raise SystemExit (e.g. a CLI-shaped config helper that sys.exit()s),
    which would otherwise sail past ``except Exception`` and take down the host.
    KeyboardInterrupt is deliberately NOT caught: the user's interrupt must reach
    the host."""
    # Session-close events run REGARDLESS of the gate so an already-open window is
    # finalized and freed even if the kill switch was flipped off mid-session (no
    # registry leak). They are cheap no-ops when nothing is open for the session.
    close = hook_name in ("on_session_end", "on_session_finalize", "on_session_reset")
    if not close and not handles_hook(hook_name):
        return
    try:
        if hook_name == "pre_llm_call":
            _open_window(kwargs)
        elif hook_name == "post_tool_call":
            _record(kwargs, _map_tool_call)
        elif hook_name == "api_request_error":
            _record(kwargs, _map_api_error)
        elif close:
            _close_session(kwargs)
    except (Exception, SystemExit):  # never let a host-API failure reach the host
        logger.warning("salience observer hook failed: %s", hook_name, exc_info=True)


# --- identity helpers --------------------------------------------------------

def _ids(kwargs: dict) -> tuple[str, str]:
    return str(kwargs.get("session_id") or ""), str(kwargs.get("turn_id") or "")


def _session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _subject(session_id: str, turn_id: str) -> str:
    """Durable arbitration key: hashed session component + turn id, bounded to a
    ref token. Same value for the window's signals and its policy (interpret needs
    them to share a subject).

    When turn_id is too long to fit intact, it is HASHED rather than truncated:
    plain truncation would alias two distinct turns that share a long prefix onto
    the same durable subject, cross-contaminating them in the persisted record."""
    head = _session_hash(session_id)[:16] + ":"
    room = MAX_TOKEN_LEN - len(head)
    tail = turn_id if len(turn_id) <= room else _session_hash(turn_id)
    return (head + tail)[:MAX_TOKEN_LEN]


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
        window = _WINDOWS.pop(session_id, None)
        if window is not None and not window.closed:
            _close_locked(window)          # emit the final directive first
        # Session is over: free its window AND its bus. Without this, a
        # long-lived host (gateway/daemon serving many distinct sessions)
        # accumulates one fully-materialized SalienceBus per session forever —
        # the one way a produce-only observer could eventually OOM the host.
        # A late hook for this session now hits _record's "no window" guard
        # and is dropped, so freeing here is safe.
        _BUSES.pop(session_id, None)
        # Free the consumer cache too: the session is over, no further turn will
        # consume its directive in-process. Omitting this would leak one Directive
        # per session on a long-lived host — the same per-session growth the _BUSES
        # pop prevents. A post-close read recovers from disk if ever needed.
        _LAST_DIRECTIVE.pop(session_id, None)


def _close_locked(window: _Window, budget: "int | None" = None) -> None:
    """Finalize a turn: interpret its accumulated signals against the produce
    policy and emit the resulting directive to the bus. Idempotent.

    ``budget`` is the policy's operator floor (A4). The produce-side closes
    (turn rollover, session end) pass ``None`` and resolve the configured budget
    via ``_operator_budget()``; the consumer's finalize-on-read passes the caller's
    resolved ``default`` so the directive is floored at THIS turn's actual budget.
    The emitted directive is cached in ``_LAST_DIRECTIVE`` for the consumer to read.
    Caller must hold ``_LOCK``."""
    if window.closed:
        return
    window.closed = True
    try:
        if budget is None:
            budget = _operator_budget()   # once: no min>max skew, no repeated I/O
        policy = issue_policy(
            "salience.observer.v0",   # policy_id
            window.subject,           # subject (matches the signals)
            (),                       # granted_capabilities: NONE (P-01)
            budget,                   # min_budget (A4: operator floor)
            budget,                   # max_budget (v0: pinned to the floor — widening
                                      # the window is a future behavior-changing change)
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
        # Cache the recorded decision for the compute-budget consumer (turn N reads
        # the directive of the turn that just closed). Only after a successful emit,
        # so a half-failed close never leaves a phantom directive to be consumed.
        _LAST_DIRECTIVE[window.session_id] = directive
    except (Exception, SystemExit):  # consistent with the gate + dispatch containment
        # A failed finalize must fail OPEN, not leave the PRIOR turn's cached
        # directive to be consumed as this turn's decision (that would apply a
        # 2-turns-stale budget). Drop it so the consumer falls back to default.
        _LAST_DIRECTIVE.pop(window.session_id, None)
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
    sys.exit()s on a missing key); falls back to a safe constant.

    Memoized: resolved once per process. It is only ever called from
    _close_locked while _LOCK is held, so the module-global cache needs no extra
    synchronization; reading once also removes any min>max skew from a config
    change landing between two reads, and keeps config disk-I/O off the hot path
    after the first finalize. PR-H2 owns getting this exactly right (it is the
    consumer of the budget)."""
    global _OPERATOR_BUDGET_CACHE
    if _OPERATOR_BUDGET_CACHE is not None:
        return _OPERATOR_BUDGET_CACHE
    budget = _DEFAULT_BUDGET
    try:
        from hermes_cli.config import read_raw_config_readonly

        cfg = read_raw_config_readonly() or {}
    except (Exception, SystemExit):
        cfg = {}
    for path in (("agent", "max_iterations"), ("max_iterations",), ("agent", "iteration_budget")):
        node: Any = cfg
        for part in path:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                node = None
                break
        if isinstance(node, int) and not isinstance(node, bool) and node > 0:
            budget = node
            break
    _OPERATOR_BUDGET_CACHE = budget
    return budget


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


# --- consumer: compute budget (PR-H2, the first governed knob) ----------------
#
# The only path WIRED to change behavior (inert in v0 — the directive echoes the
# operator budget; see the module docstring). Everything above records;
# `bounded_iterations` READS the recorded directive and applies its compute_budget
# to the host's per-turn iteration budget. It is a consumer, not a decider
# (Finding D): it applies the policy-clamped value verbatim — no re-clamp, no
# re-derivation from raw salience — and fails open to the caller's `default` on any
# failure, absence, deny-shaped directive, or switch-off.


def _consume_enabled() -> bool:
    """Consumption gate. The master switch (``salience_enabled``) must be on AND the
    consumption-specific kill switch ``salience.consume_compute`` (default ON as of
    PR-H2). ``enabled: false`` disables the whole subsystem; ``consume_compute:
    false`` disables only this behavior-changing consumer while the produce path
    keeps recording."""
    if not salience_enabled():
        return False
    return _config_flag("consume_compute", True)


def _ensure_template_valid() -> None:
    """One-time WELL-FORMEDNESS check of the hardcoded v0 policy template, run on
    first consume, logging LOUDLY if it fails.

    Honest scope: this is NOT config validation. No template knob is config-wired
    yet (only ``enabled`` / ``consume_compute`` exist), and the template is built
    from in-module constants and self-signed with ``_POLICY_KEY``, so today
    ``verify_policy`` can only fail if those CONSTANTS are edited into an incoherent
    shape (e.g. ``max_retention`` outside the ladder, ``min > max``) — a construction
    regression. It is a cheap tripwire for that, and the seam where real
    config-driven template validation will live once knobs are plumbed. Either way a
    rejected template can never brick the agent: it makes every window hard-deny,
    which the deny-shaped guard turns into the operator default. Caller holds
    ``_LOCK`` (reads the operator budget cache)."""
    global _TEMPLATE_VALIDATED
    if _TEMPLATE_VALIDATED is not None:
        return
    try:
        budget = _operator_budget()
        policy = issue_policy(
            "salience.observer.v0", "salience.template.probe", (),
            budget, budget, 0, 3, "semantic", False, 2, 0.5, False, _POLICY_KEY,
        )
        _TEMPLATE_VALIDATED = bool(verify_policy(policy, _POLICY_KEY))
    except (Exception, SystemExit):
        _TEMPLATE_VALIDATED = False
    if not _TEMPLATE_VALIDATED:
        logger.error(
            "salience: v0 policy template failed verify_policy — a construction "
            "regression; the compute-budget consumer falls back to the operator "
            "default and governs nothing"
        )


def _directive_budget(source: Any) -> "int | None":
    """The governed compute budget from a recorded directive, or None if it should
    be treated as ABSENT. ``source`` is either a live ``Directive`` object (from the
    in-memory cache) or a replayed payload ``dict`` (from the session JSONL).

    Deny-shaped guard (A5): a hard-deny withholds its subject/policy_id and carries
    ``compute_budget=0`` — treating any of those markers (or a non-int / bool /
    sub-1 budget) as absent keeps a hard-deny, a rejected template, or a lost policy
    key from ever bricking the agent at ``max_iterations < 1``. This CONSUMES the
    withhold markers the decider stamped; it does not re-decide."""
    if source is None:
        return None
    if isinstance(source, dict):
        subject = source.get("subject")
        policy_id = source.get("policy_id")
        budget = source.get("compute_budget")
    else:
        subject = getattr(source, "subject", None)
        policy_id = getattr(source, "policy_id", None)
        budget = getattr(source, "compute_budget", None)
    if not subject or not policy_id:
        return None
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        return None
    return budget


def _budget_from_disk(session_id: str) -> "int | None":
    """Cold-restart fallback: recover the last recorded budget from the session
    JSONL when the in-memory caches are empty (a fresh process over an existing
    session).

    Runs ONLY when no bus is cached for this session. In-process, the authority is
    ``_LAST_DIRECTIVE``; if a bus is already cached but ``_LAST_DIRECTIVE`` is empty
    the last close FAILED, and reading a stale on-disk directive would both bypass
    the replay verification (a cached ``_bus_for`` does not re-verify the current
    file) and apply a 2-turns-stale budget — so we return None (⇒ default) instead.

    On the cold path, constructing the bus replays AND verifies the whole chain,
    raising on a corrupt/tampered tail (caught by the caller ⇒ default). The last
    directive is then read from the bus's VERIFIED in-memory store — no second
    independent parse of the file (which would be redundant with the replay and
    could pick a stale subject if the file were truncated between the two reads),
    and no reliance on the subject-keyed public accessor we cannot key without a
    turn id. The recovered directive is deep-copied and promoted into
    ``_LAST_DIRECTIVE`` (a state-mutating side effect) so this once-per-restart cold
    path need not repeat on a second read. Caller holds ``_LOCK``."""
    if session_id in _BUSES:
        return None
    from pathlib import Path

    from hermes_constants import get_hermes_home

    path = Path(get_hermes_home()) / "salience" / (_session_hash(session_id) + ".jsonl")
    if not path.exists():
        return None
    bus = _bus_for(session_id)  # constructs ⇒ replay + verify (raises on a corrupt chain)
    # The verified, in-order directive store the replay just built: (hash, payload)
    # tuples, oldest first. getattr keeps us fail-open if the vendored bus ever
    # renames it (⇒ None ⇒ default). No public accessor serves "the last directive
    # regardless of subject", so we read the store the replay verified.
    directives = getattr(bus, "_directives", None)
    if not directives:
        return None
    # Deep-copy out of the bus's internal store before caching: directives[-1][1] is
    # the SAME dict the bus holds in _directives/_entries, and directives_for()
    # deep-copies for exactly this reason — never hand a consumer a mutable alias into
    # the verified audit record.
    payload = copy.deepcopy(directives[-1][1])
    # Promote the verified recovery into the in-memory cache: the cold disk path
    # runs only once per restart (a warm _BUSES short-circuits it), so without this a
    # second read before the next close would drop the recovered value to default
    # (grok-F2).
    _LAST_DIRECTIVE[session_id] = payload
    return _directive_budget(payload)


def _resolve_bounded(session_id: str, default: int) -> "int | None":
    """Finalize the prior turn's window (A3), then read its recorded budget.
    Returns None when there is nothing to apply. Holds ``_LOCK`` for the whole
    read-modify-read so a concurrent hook cannot open/close a window mid-resolve."""
    with _LOCK:
        _ensure_template_valid()
        # Policy floor for the finalize-on-read close = THIS turn's resolved budget
        # (A4) when it is a sane positive int; otherwise the configured operator
        # budget (_operator_budget is only safe to read under _LOCK). Note: closing
        # turn N-1's window here floors its DURABLE directive at turn N's budget, so
        # if the operator budget changed between turns the record is floored to the
        # reader's value — harmless (the value is consumed immediately and v0 echoes
        # the operator budget); tightening the audit provenance is deferred.
        floor = default if (isinstance(default, int) and not isinstance(default, bool)
                            and default > 0) else _operator_budget()
        # Finalize-on-read (A3): close the PRIOR turn's still-open window NOW, at the
        # between-turn boundary, so turn N applies turn N-1's directive rather than
        # N-2's. This is exactly the rollover close, pulled one step earlier than the
        # next turn's pre_llm_call; idempotent when the window is already closed.
        window = _WINDOWS.get(session_id)
        if window is not None and not window.closed:
            _close_locked(window, budget=floor)
        directive = _LAST_DIRECTIVE.get(session_id)
        if directive is not None:
            return _directive_budget(directive)
        return _budget_from_disk(session_id)


def bounded_iterations(session_id: str, default: int) -> int:
    """Bound this turn's iteration budget by the directive recorded for the prior
    turn — the first governed knob (wired live; inert in v0). Called once at turn
    start, immediately before the host rebuilds its ``IterationBudget`` (between-turn
    only, Finding F). Precisely, it applies the most recently RECORDED turn's
    directive: normally that is the immediately prior turn, but a turn that aborts
    before opening its window records nothing, so an earlier turn may be the latest.

    Fails OPEN to ``default`` on EVERYTHING: subsystem off, consumption kill switch
    off, no prior directive, a deny-shaped directive, or any error. ``default`` is
    the host's positive iteration budget; a non-int / bool / non-positive ``default``
    is out of contract and returned UNTOUCHED (the consumer never manufactures or
    clamps a budget for a bad host value). Given a valid positive ``default``, the
    returned budget is never < 1. In the v0 config the directive echoes the
    operator's own budget (pinned window +
    ATTENTION unmapped), so this is behavior-preserving until a future change widens
    the policy window and maps a budget-moving facet (its own review). Consumer, not
    decider (Finding D): the returned value is the recorded, policy-clamped budget
    applied verbatim — never re-clamped against ``default`` or config, up OR down."""
    if not isinstance(default, int) or isinstance(default, bool) or default < 1:
        # Out of contract (non-int / bool / non-positive): return it UNTOUCHED and do
        # not finalize-on-read. The consumer never manufactures or clamps a budget
        # for a bad host value — a max_iterations < 1 is the host's own bug, not ours
        # to silently paper over with an operator floor.
        return default
    try:
        if not _consume_enabled() or not session_id:
            return default
        budget = _resolve_bounded(session_id, default)
        return budget if budget is not None else default
    except (Exception, SystemExit):  # consumer must never break the turn
        logger.warning("salience consumer: bounded_iterations failed", exc_info=True)
        return default


def _reset_for_tests() -> None:
    """Drop all in-memory windows/buses, the last-directive cache, and the budget +
    template-validation caches. Test-only; never called in production."""
    global _OPERATOR_BUDGET_CACHE, _TEMPLATE_VALIDATED
    with _LOCK:
        _WINDOWS.clear()
        _BUSES.clear()
        _LAST_DIRECTIVE.clear()
        _OPERATOR_BUDGET_CACHE = None
        _TEMPLATE_VALIDATED = None
