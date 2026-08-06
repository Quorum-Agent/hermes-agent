"""Tests for the Stage-2 compute-budget CONSUMER (PR-H2).

The consumer is ``salience_observer.bounded_iterations`` — the first governed knob:
it reads the directive recorded for the PRIOR turn and applies its ``compute_budget``
to the host's per-turn iteration budget, immediately before ``turn_context`` rebuilds
``IterationBudget`` (between-turn only, Finding F).

What these tests pin:
* it applies the recorded, policy-clamped budget VERBATIM (consumer, not decider —
  no re-clamp against config);
* it finalizes the prior turn's open window on read, so turn N applies turn N-1's
  directive, never a stale one (A3);
* deny-shaped directives, a switched-off subsystem, the consumption kill switch, a
  missing session, and any error all fail OPEN to the caller's ``default`` — the
  consumer can never brick the agent (A5);
* it recovers the last budget from the session JSONL after a restart, and fails
  closed to ``default`` on a corrupt tail rather than trusting it (grok-F8);
* it never leaks its per-session cache, and the call site precedes the budget rebuild.

Harness mirrors test_salience_observer.py. The shared conftest disables the gate
suite-wide (the subsystem is default-ON in the product); these tests opt back in.
"""

from pathlib import Path

import pytest

import hermes_constants
import product_identity
from agent.iteration_budget import IterationBudget
from hermes_cli import config as hermes_config
from hermes_cli.observability import salience_observer as so

_REAL_SALIENCE_ENABLED = so.salience_enabled


@pytest.fixture(autouse=True)
def _reset_state():
    so._reset_for_tests()
    yield
    so._reset_for_tests()


def _use_config(monkeypatch, tmp_path, cfg, gate=True):
    """Opt in: point the bus at a temp home, force the master gate, and serve
    ``cfg`` as the raw config (which carries the ``salience`` block AND any
    ``agent.max_iterations`` the operator-budget read looks at)."""
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(so, "salience_enabled", (lambda: gate), raising=False)
    monkeypatch.setattr(hermes_config, "read_raw_config_readonly", lambda: cfg, raising=False)


@pytest.fixture
def home(monkeypatch, tmp_path):
    _use_config(monkeypatch, tmp_path, {"salience": {"enabled": True}}, gate=True)
    return tmp_path


def _bus_file(tmp_path, session_id):
    return Path(tmp_path) / "salience" / (so._session_hash(session_id) + ".jsonl")


def _make_directive(subject, budget):
    """A genuine Directive carrying a chosen compute_budget (v0 pins
    min==max==budget, and no ATTENTION signal ⇒ compute_budget == budget)."""
    policy = so.issue_policy(
        "p", subject, (), budget, budget, 0, 3, "semantic", False, 2, 0.5, False,
        so._POLICY_KEY,
    )
    return so.interpret(policy, (), so._POLICY_KEY)


def _open(session_id, turn_id):
    so._open_window({"session_id": session_id, "task_id": "t", "turn_id": turn_id})


def _record_write(session_id, turn_id):
    so._record({"session_id": session_id, "turn_id": turn_id,
                "tool_name": "write_file", "status": "ok"}, so._map_tool_call)


# --- applies the recorded value, not the default -----------------------------

def test_applies_recorded_budget_verbatim(home):
    # A directive recorded for the prior turn carries budget 7; with no open window
    # to finalize, the consumer reads it and applies it VERBATIM — it is NOT
    # re-clamped toward the caller's default of 10 (consumer, not decider).
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 7)
    assert so.bounded_iterations("s", 10) == 7


def test_no_reclamp_directive_below_default(home):
    # Sabotage frame: directive says 3 where config/default would say 100 ⇒ the
    # recorded 3 wins. Any re-clamp against the default reds this.
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 3)
    assert so.bounded_iterations("s", 100) == 3


# --- deny-shaped ⇒ default (A5) ----------------------------------------------

def test_hard_deny_directive_falls_back_to_default(home):
    # interpret(None) is the hard-deny: blank subject/policy_id, compute_budget 0.
    so._LAST_DIRECTIVE["s"] = so.interpret(None, (), so._POLICY_KEY)
    assert so.bounded_iterations("s", 10) == 10


def test_zero_budget_directive_falls_back_to_default(home):
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 0)
    assert so.bounded_iterations("s", 10) == 10


def test_directive_budget_guard_shapes():
    # The deny-shaped guard, in isolation: object and dict shapes both honored;
    # blank/absent subject or policy_id, and a bool/sub-1/non-int budget, ⇒ None.
    assert so._directive_budget(_make_directive("subj", 5)) == 5
    assert so._directive_budget(
        {"subject": "subj", "policy_id": "p", "compute_budget": 5}) == 5
    assert so._directive_budget(
        {"subject": "", "policy_id": "p", "compute_budget": 5}) is None
    assert so._directive_budget(
        {"subject": "subj", "policy_id": "", "compute_budget": 5}) is None
    assert so._directive_budget(
        {"subject": "subj", "policy_id": "p", "compute_budget": 0}) is None
    assert so._directive_budget(
        {"subject": "subj", "policy_id": "p", "compute_budget": True}) is None
    assert so._directive_budget(
        {"subject": "subj", "policy_id": "p", "compute_budget": "5"}) is None
    assert so._directive_budget(None) is None


# --- finalize-on-read: turn N applies turn N-1 (A3) --------------------------

def test_finalize_on_read_closes_prior_window(home):
    _open("s", "u1")
    _record_write("s", "u1")
    assert "s" not in so._LAST_DIRECTIVE            # not closed yet

    applied = so.bounded_iterations("s", 10)

    # the prior window was finalized on read: its directive is now on the bus and
    # cached, and the returned budget is the one it was closed with (floor == default)
    assert so._WINDOWS["s"].closed is True
    assert len(so._bus_for("s").directives_for(so._subject("s", "u1"))) == 1
    assert applied == 10


def test_three_turns_read_prior_not_stale(home):
    # Model the real cadence: each turn calls bounded_iterations FIRST (line ~491),
    # THEN pre_llm_call opens that turn's window (line ~1054). Distinct per-turn
    # defaults make the A3 property observable: turn 3 must read turn 2's window
    # (30), not the stale turn-1 directive (20). Deleting finalize-on-read makes
    # turn 3 return 20 and reds this.
    applied1 = so.bounded_iterations("s", 10)   # no prior turn
    _open("s", "u1"); _record_write("s", "u1")

    applied2 = so.bounded_iterations("s", 20)   # finalizes u1
    _open("s", "u2"); _record_write("s", "u2")

    applied3 = so.bounded_iterations("s", 30)   # finalizes u2 (NOT u1)

    assert (applied1, applied2, applied3) == (10, 20, 30)
    # each turn's directive is recorded under its OWN subject with its own budget
    bus = so._bus_for("s")
    assert bus.directives_for(so._subject("s", "u1"))[0]["compute_budget"] == 20
    assert bus.directives_for(so._subject("s", "u2"))[0]["compute_budget"] == 30


# --- restart fallback (grok-F8) ----------------------------------------------

def test_restart_recovers_budget_from_disk(monkeypatch, tmp_path):
    # Produce + close a session (writes the directive to the JSONL and frees the
    # in-memory caches), then simulate a restart by clearing all module state. The
    # consumer must recover the last recorded budget from disk, not fall to default.
    _use_config(monkeypatch, tmp_path,
                {"agent": {"max_iterations": 7}, "salience": {"enabled": True}}, gate=True)
    _open("s", "u")
    _record_write("s", "u")
    so._close_session({"session_id": "s"})       # directive(budget 7) persisted
    assert _bus_file(tmp_path, "s").exists()

    so._reset_for_tests()                          # restart: in-memory gone, file remains
    assert so.bounded_iterations("s", 10) == 7     # recovered from the verified file


def test_restart_corrupt_tail_fails_closed_to_default(monkeypatch, tmp_path):
    _use_config(monkeypatch, tmp_path,
                {"agent": {"max_iterations": 7}, "salience": {"enabled": True}}, gate=True)
    _open("s", "u")
    _record_write("s", "u")
    so._close_session({"session_id": "s"})
    with open(_bus_file(tmp_path, "s"), "a", encoding="utf-8") as fh:
        fh.write("}{ this is not valid json\n")   # corrupt the tail

    so._reset_for_tests()
    # bus replay raises on the corrupt tail; the consumer catches it and returns the
    # operator default rather than trusting an unverifiable record or crashing.
    assert so.bounded_iterations("s", 10) == 10


def test_restart_with_no_file_returns_default(monkeypatch, tmp_path):
    _use_config(monkeypatch, tmp_path, {"salience": {"enabled": True}}, gate=True)
    # nothing ever produced for this session ⇒ no file ⇒ default, no bus created
    assert so.bounded_iterations("never-seen", 10) == 10
    assert not (Path(tmp_path) / "salience").exists()


# --- gating ------------------------------------------------------------------

def test_consume_kill_switch_leaves_budget_and_window_untouched(monkeypatch, tmp_path):
    # consume_compute:false ⇒ the behavior-changing consumer is off: default is
    # returned AND the prior window is not finalized (produce path keeps its own
    # cadence — the master switch is still on).
    _use_config(monkeypatch, tmp_path,
                {"salience": {"enabled": True, "consume_compute": False}}, gate=True)
    _open("s", "u")
    _record_write("s", "u")
    assert so.bounded_iterations("s", 10) == 10
    assert so._WINDOWS["s"].closed is False        # not finalized by the consumer


def test_subsystem_off_returns_default(monkeypatch, tmp_path):
    _use_config(monkeypatch, tmp_path, {"salience": {"enabled": True}}, gate=False)
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 7)
    assert so.bounded_iterations("s", 10) == 10    # master gate off ⇒ inert


def test_real_gate_off_edition_returns_default(monkeypatch, tmp_path):
    # Exercise the REAL gate: a stock (non-Quorum) edition consumes nothing.
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(so, "salience_enabled", _REAL_SALIENCE_ENABLED, raising=False)
    monkeypatch.setattr(product_identity, "IS_QUORUM_EDITION", False, raising=False)
    monkeypatch.setattr(hermes_config, "read_raw_config_readonly", lambda: {}, raising=False)
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 7)
    assert so.bounded_iterations("s", 10) == 10


def test_empty_session_returns_default(home):
    assert so.bounded_iterations("", 10) == 10


@pytest.mark.parametrize("bad_default", ["x", True, None, 3.5])
def test_non_int_default_returned_unchanged(home, bad_default):
    # The caller passes agent.max_iterations; if it is ever not a plain int, leave
    # it exactly as-is rather than inventing a budget.
    assert so.bounded_iterations("s", bad_default) is bad_default


# --- propagation into the host budget ----------------------------------------

def test_applied_value_propagates_into_iteration_budget(home):
    # Pins the :491 mechanic the call site relies on: rebinding max_iterations is
    # sufficient because IterationBudget is rebuilt from it. Mirrors the two source
    # lines exactly.
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 7)
    applied = so.bounded_iterations("s", 10)
    budget = IterationBudget(applied)              # what turn_context does at :491
    assert budget.max_total == applied == 7


def test_call_site_precedes_budget_rebuild():
    # Structural adjacency guard (a full-turn harness is out of scope for a unit
    # test): the consumer must assign agent.max_iterations immediately before the
    # IterationBudget rebuild, with no reassignment in between. A reorder that moves
    # the rebuild above the consumer — silently bypassing it — reds this.
    src = Path(__file__).resolve().parents[2] / "agent" / "turn_context.py"
    lines = src.read_text(encoding="utf-8").splitlines()
    call = next(i for i, ln in enumerate(lines)
                if "bounded_iterations(" in ln and "agent.max_iterations" in ln)
    rebuild = next(j for j, ln in enumerate(lines)
                   if j > call and "IterationBudget(agent.max_iterations)" in ln)
    assert rebuild - call <= 12
    between = lines[call + 1:rebuild]
    assert not any("agent.max_iterations =" in ln for ln in between)


# --- no leak, no crash -------------------------------------------------------

def test_consumer_cache_freed_on_session_close(home):
    _open("s", "u")
    _record_write("s", "u")
    so.bounded_iterations("s", 10)                 # finalize-on-read caches a directive
    assert "s" in so._LAST_DIRECTIVE

    so._close_session({"session_id": "s"})
    # session over: the consumer cache is freed too, so a long-lived host does not
    # accumulate one Directive per session. Reverting the _LAST_DIRECTIVE pop reds this.
    assert "s" not in so._LAST_DIRECTIVE
    assert "s" not in so._WINDOWS
    assert "s" not in so._BUSES


def test_template_validation_flagged_but_consumption_survives(home, monkeypatch):
    # verify_policy failing on the probe must be surfaced (loud log, flag False) but
    # must NOT break consumption — the real close still issues a valid policy through
    # the interpreter's own verify_policy (not the module-level name patched here).
    monkeypatch.setattr(so, "verify_policy", lambda *a, **k: False, raising=False)
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 7)
    assert so.bounded_iterations("s", 10) == 7
    assert so._TEMPLATE_VALIDATED is False


def test_bounded_iterations_never_raises_on_broken_home(home, monkeypatch):
    # A host API that sys.exit()s (like the fixed get_config_value) must be
    # contained: the consumer runs at turn start and may never crash the host. With
    # no cached window/directive the resolve falls through to the disk path, which
    # calls get_hermes_home — force it to SystemExit there and assert containment.
    def _boom():
        raise SystemExit(1)

    monkeypatch.setattr(hermes_constants, "get_hermes_home", _boom, raising=False)
    assert so.bounded_iterations("s", 10) == 10    # contained ⇒ default
