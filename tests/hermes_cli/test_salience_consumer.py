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

import json
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


def test_no_reclamp_directive_above_default(home):
    # The consumer applies the recorded budget VERBATIM even when it EXCEEDS the
    # caller's default — salience must be able to RAISE iterations, not only lower
    # them. A downward clamp `min(budget, default)` — the natural misreading of a
    # function named `bounded_iterations` — would silently cap this at 10 and reds.
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u"), 40)
    assert so.bounded_iterations("s", 10) == 40


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


def test_second_read_returns_cached_directive_not_default(home):
    # In v0 a live finalize-on-read returns the operator's own default, so no single
    # live call can distinguish "read the cached directive" from "returned default".
    # A SECOND read with a DIFFERENT default does: the window is already closed, so
    # the consumer must return the CACHED directive's budget (the first close's floor
    # 20), not the new default 99. Removing the `_LAST_DIRECTIVE[...] = directive`
    # write in _close_locked drops the second read to default via _budget_from_disk
    # (bus warm ⇒ None ⇒ 99) and reds — the mutation the three-turn test cannot see.
    _open("s", "u1")
    _record_write("s", "u1")
    first = so.bounded_iterations("s", 20)     # closes u1 at floor 20, caches budget 20
    assert first == 20
    second = so.bounded_iterations("s", 99)    # u1 already closed ⇒ read the cache (20)
    assert second == 20


def test_failed_close_fails_open_not_stale(home, monkeypatch):
    # A prior turn's directive is cached (budget 7). This turn's window is open and
    # its finalize-on-read close FAILS (interpret raises). The consumer must fail
    # OPEN to default — NOT apply the stale prior directive (7) as this turn's
    # decision (a 2-turns-stale budget). Dropping the `_LAST_DIRECTIVE.pop` in
    # _close_locked's except leaves 7 cached and reds.
    so._LAST_DIRECTIVE["s"] = _make_directive(so._subject("s", "u1"), 7)
    _open("s", "u2")
    _record_write("s", "u2")                   # caches the bus for "s"

    def _boom(*a, **k):
        raise RuntimeError("finalize failed")

    monkeypatch.setattr(so, "interpret", _boom, raising=False)
    assert so.bounded_iterations("s", 10) == 10   # fail-open, not the stale 7


def test_three_turns_read_prior_not_stale(home):
    # Model the real cadence: each turn calls bounded_iterations FIRST (line ~491),
    # THEN pre_llm_call opens that turn's window (line ~1054). Distinct per-turn
    # defaults make the A3 property observable: turn 3 must read turn 2's window
    # (30), not the stale turn-1 directive. Deleting finalize-on-read makes turn 3
    # read u1 — which _open("u2")'s rollover closed at the operator budget (25, the
    # _DEFAULT_BUDGET under this fixture's config) — so turn 3 returns 25, not 30,
    # and reds. The directives_for(u1)==20 assertion also pins the A4 floor binding
    # (a real finalize-on-read closes u1 at the caller default 20, not operator 25).
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


def test_cold_recovery_is_cached_for_second_read(monkeypatch, tmp_path):
    # The cold disk path runs only once per restart (a warm _BUSES short-circuits
    # it). The verified recovery must be promoted to the in-memory cache so a second
    # read before the next close still returns it — a turn aborted before opening its
    # window must not silently drop the recovered budget back to default (grok-F2).
    _use_config(monkeypatch, tmp_path,
                {"agent": {"max_iterations": 7}, "salience": {"enabled": True}}, gate=True)
    _open("s", "u")
    _record_write("s", "u")
    so._close_session({"session_id": "s"})         # directive(7) persisted
    so._reset_for_tests()

    assert so.bounded_iterations("s", 10) == 7      # cold recovery
    # second read, no window opened/closed in between: without the promote the
    # warm-_BUSES guard drops it to default (10); with it, the cache still holds 7.
    assert so.bounded_iterations("s", 10) == 7


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


def test_restart_nontail_tamper_fails_closed(monkeypatch, tmp_path):
    # The integrity guarantee must come from the bus's replay-verify, NOT from the
    # observer's own json.loads. Build a two-directive file and corrupt a NON-TAIL
    # directive's hash with still-VALID JSON: only SalienceBus._replay's digest/chain
    # check can detect that (the observer's own parse reads it fine). Replay raises on
    # open ⇒ consumer falls back to default. Neuter the replay integrity check and the
    # tampered file is accepted, recovering the tail directive's budget (7) ⇒ this reds.
    _use_config(monkeypatch, tmp_path,
                {"agent": {"max_iterations": 7}, "salience": {"enabled": True}}, gate=True)
    _open("s", "u1")
    _record_write("s", "u1")
    _open("s", "u2")                              # rollover closes u1 ⇒ directive(u1) persisted
    _record_write("s", "u2")
    so._close_session({"session_id": "s"})        # closes u2 ⇒ directive(u2) persisted (the tail)

    path = _bus_file(tmp_path, "s")
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, ln in enumerate(lines) if json.loads(ln).get("kind") == "directive")
    assert idx < len(lines) - 1                   # confirm it is genuinely non-tail
    entry = json.loads(lines[idx])
    entry["hash"] = "0" * 64                       # valid JSON, wrong hash
    lines[idx] = json.dumps(entry, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    so._reset_for_tests()
    assert so.bounded_iterations("s", 10) == 10    # replay raises ⇒ fail-closed to default


def test_restart_fallback_skipped_when_bus_cached(monkeypatch, tmp_path):
    # Guard: the disk fallback runs only on a COLD restart (no cached bus). If a bus
    # is cached in-process but _LAST_DIRECTIVE is empty (a close that failed), reading
    # the stale on-disk directive would both skip replay re-verification (a cached
    # _bus_for does not re-verify) and apply a stale budget — so the fallback must be
    # skipped and default returned. Dropping the `session_id in _BUSES` guard would
    # read the persisted 7 and red this.
    _use_config(monkeypatch, tmp_path,
                {"agent": {"max_iterations": 7}, "salience": {"enabled": True}}, gate=True)
    _open("s", "u")
    _record_write("s", "u")
    so._close_session({"session_id": "s"})         # directive(7) persisted
    assert _bus_file(tmp_path, "s").exists()

    so._reset_for_tests()
    so._bus_for("s")                               # simulate: a bus cached, no successful close
    assert "s" in so._BUSES and "s" not in so._LAST_DIRECTIVE
    assert so.bounded_iterations("s", 10) == 10    # guard ⇒ no disk read, default


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


def test_template_validation_flagged_but_consumption_survives(home, monkeypatch, caplog):
    import logging

    # Force the well-formedness probe to fail. The real finalize-on-read close must
    # still succeed (it issues its policy through the INTERPRETER's own verify_policy,
    # not the module-level name patched here), so consumption is unaffected — while
    # the failure is surfaced loudly.
    monkeypatch.setattr(so, "verify_policy", lambda *a, **k: False, raising=False)
    _open("s", "u")
    _record_write("s", "u")
    with caplog.at_level(logging.ERROR, logger="hermes_cli.observability.salience_observer"):
        applied = so.bounded_iterations("s", 10)
    assert applied == 10                      # real close still produced a usable directive
    assert so._WINDOWS["s"].closed is True    # finalize-on-read genuinely ran
    assert so._TEMPLATE_VALIDATED is False    # probe recorded the failure
    assert any("policy template failed verify_policy" in r.getMessage()
               for r in caplog.records)       # …and did so loudly


def test_bounded_iterations_never_raises_on_broken_home(home, monkeypatch):
    # A host API that sys.exit()s (like the fixed get_config_value) must be
    # contained: the consumer runs at turn start and may never crash the host. With
    # no cached window/directive the resolve falls through to the disk path, which
    # calls get_hermes_home — force it to SystemExit there and assert containment.
    def _boom():
        raise SystemExit(1)

    monkeypatch.setattr(hermes_constants, "get_hermes_home", _boom, raising=False)
    assert so.bounded_iterations("s", 10) == 10    # contained ⇒ default
