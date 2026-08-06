"""Tests for the Stage-2 salience producer (hermes_cli/observability/salience_observer.py).

Covers the things that make it safe: (1) it only produces against an open window
with a matching turn id (fail-closed attribution); (2) it is gated (Quorum Edition +
config, default ON, kill switch) and otherwise completely inert; (3) the durable
record hashes the session id and verifies as a chain; (4) the emitted directive binds
the operator budget; (5) a finished session frees its in-memory state (no per-session
leak on a long-lived host). The E2E drives a REAL tool-dispatch emitter (which
self-gates on ``has_hook``) rather than calling the hook directly — calling the hook
directly would enter below the gate and prove nothing about the wiring.

Note: the shared conftest disables the observer's gate suite-wide (it is default-ON
in the product and must be inert in unrelated tests). These tests opt back in — the
gate-logic tests restore the real ``salience_enabled`` and drive its inputs; the
produce tests force the gate open or call the produce internals directly.
"""

from pathlib import Path

import pytest

import hermes_constants
import product_identity
from hermes_cli import config as hermes_config
from hermes_cli.observability import salience_observer as so
from salienceos.interpreter.bus import SalienceBus

# The genuine gate function, captured before the conftest fixture patches it.
_REAL_SALIENCE_ENABLED = so.salience_enabled


@pytest.fixture(autouse=True)
def _reset_state():
    so._reset_for_tests()
    yield
    so._reset_for_tests()


def _force_gate(monkeypatch, tmp_path, open_gate):
    """Opt in: force the gate open/closed and point the bus at a temp home."""
    monkeypatch.setattr(so, "salience_enabled", (lambda: open_gate), raising=False)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(
        hermes_config, "read_raw_config_readonly",
        lambda: {"salience": {"enabled": open_gate}}, raising=False,
    )


@pytest.fixture
def home(monkeypatch, tmp_path):
    _force_gate(monkeypatch, tmp_path, True)
    return tmp_path


def _real_gate(monkeypatch):
    """Restore the real gate so its own logic can be exercised."""
    monkeypatch.setattr(so, "salience_enabled", _REAL_SALIENCE_ENABLED, raising=False)


def _bus_file(tmp_path, session_id):
    return Path(tmp_path) / "salience" / (so._session_hash(session_id) + ".jsonl")


# --- gating ------------------------------------------------------------------

def test_enabled_by_default_when_config_absent(monkeypatch):
    _real_gate(monkeypatch)
    monkeypatch.setattr(product_identity, "IS_QUORUM_EDITION", True, raising=False)
    monkeypatch.setattr(hermes_config, "read_raw_config_readonly", lambda: {}, raising=False)
    assert so.salience_enabled() is True  # default ON


def test_kill_switch_disables(monkeypatch):
    _real_gate(monkeypatch)
    monkeypatch.setattr(product_identity, "IS_QUORUM_EDITION", True, raising=False)
    monkeypatch.setattr(
        hermes_config, "read_raw_config_readonly",
        lambda: {"salience": {"enabled": False}}, raising=False,
    )
    assert so.salience_enabled() is False
    assert so.handles_hook("post_tool_call") is False


def test_not_quorum_edition_is_dark(monkeypatch):
    _real_gate(monkeypatch)
    monkeypatch.setattr(product_identity, "IS_QUORUM_EDITION", False, raising=False)
    monkeypatch.setattr(hermes_config, "read_raw_config_readonly", lambda: {}, raising=False)
    assert so.salience_enabled() is False


def test_unreadable_config_fails_closed(monkeypatch):
    _real_gate(monkeypatch)
    monkeypatch.setattr(product_identity, "IS_QUORUM_EDITION", True, raising=False)

    def _boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(hermes_config, "read_raw_config_readonly", _boom, raising=False)
    assert so.salience_enabled() is False  # can't read config ⇒ stay dark


def test_only_mapped_hooks_are_handled(home):
    # `home` forces the gate open; handles_hook then reflects the hook allowlist.
    assert so.handles_hook("post_tool_call") is True
    assert so.handles_hook("pre_llm_call") is True
    assert so.handles_hook("api_request_error") is True
    # not in the produce map — must stay False even when enabled
    assert so.handles_hook("pre_verify") is False
    assert so.handles_hook("transform_tool_result") is False


# --- signal mapping ----------------------------------------------------------

def test_mapping_by_facet():
    subject = "subj"
    err = so._map_tool_call({"tool_name": "run_shell", "status": "error",
                             "error_type": "tool_error"}, subject)
    approval = so._map_tool_call({"tool_name": "request_approval", "status": "ok"}, subject)
    mutation = so._map_tool_call({"tool_name": "write_file", "status": "success"}, subject)
    read = so._map_tool_call({"tool_name": "read_file", "status": "success"}, subject)

    assert [s.facet for s in err] == ["verification"]
    assert [s.facet for s in approval] == ["risk"]
    assert [s.facet for s in mutation] == ["memory"]
    assert read == []  # a plain read is not salient in the v0 map

    api = so._map_api_error({"retryable": False, "provider": "anthropic"}, subject)
    assert [s.facet for s in api] == ["verification"]
    # provenance carries only bounded ref tokens, never bodies/args
    assert all(len(p) <= so.MAX_TOKEN_LEN for p in api[0].provenance)


# --- fail-closed attribution -------------------------------------------------

def test_records_only_against_matching_open_window(home):
    subject = so._subject("s", "u")

    # no window open yet -> dropped
    so._record({"session_id": "s", "turn_id": "u", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    assert so._bus_for("s").signals_for(subject) == ()

    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u"})
    # wrong turn id -> dropped (the load-bearing attribution guard: deleting the
    # turn_id check in _record makes the final count 2 and reds this test)
    so._record({"session_id": "s", "turn_id": "WRONG", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    # matching -> recorded
    so._record({"session_id": "s", "turn_id": "u", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)

    assert len(so._bus_for("s").signals_for(subject)) == 1
    # (empty session/turn ids are covered by test_no_ids_no_window.)


def test_no_ids_no_window(home):
    so._open_window({"session_id": "", "task_id": "t", "turn_id": "u"})
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": ""})
    assert so._WINDOWS == {}  # neither opened


# --- subject hashing ---------------------------------------------------------

def test_subject_hashes_session_and_is_bounded():
    subject = so._subject("super-secret-session", "turn-9")
    assert "super-secret-session" not in subject
    assert subject.endswith(":turn-9")
    assert 0 < len(subject) <= so.MAX_TOKEN_LEN
    assert so._subject("super-secret-session", "turn-9") == subject  # deterministic


# --- window finalize + directive content -------------------------------------

def test_close_emits_one_directive_and_is_idempotent(home):
    subject = so._subject("s", "u")
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u"})
    so._record({"session_id": "s", "turn_id": "u", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    so._close_session({"session_id": "s"})
    so._close_session({"session_id": "s"})  # idempotent

    dirs = so._bus_for("s").directives_for(subject)
    assert len(dirs) == 1
    # v0 is product-dormant: adaptation disallowed, never an inhibitor
    assert dirs[0]["adaptation_rationale"] == "policy_disallowed"


def test_new_turn_finalizes_previous(home):
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u1"})
    so._record({"session_id": "s", "turn_id": "u1", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    # opening u2 must finalize u1 first (turn N governs N+1)
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u2"})
    assert len(so._bus_for("s").directives_for(so._subject("s", "u1"))) == 1


def test_emitted_directive_binds_operator_budget(monkeypatch, tmp_path):
    # A4: the operator's configured iteration budget is bound into the directive
    # (min==max==operator budget in v0). Reverting the A4 binding or a broken
    # _operator_budget read would change this value.
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(hermes_config, "read_raw_config_readonly",
                        lambda: {"agent": {"max_iterations": 7}}, raising=False)
    subject = so._subject("s", "u")
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u"})
    so._record({"session_id": "s", "turn_id": "u", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    so._close_session({"session_id": "s"})

    directive = SalienceBus(str(_bus_file(tmp_path, "s"))).directives_for(subject)[0]
    assert directive["compute_budget"] == 7


def test_emitted_directive_defaults_budget_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(hermes_config, "read_raw_config_readonly", lambda: {}, raising=False)
    subject = so._subject("s", "u")
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u"})
    so._record({"session_id": "s", "turn_id": "u", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    so._close_session({"session_id": "s"})

    directive = SalienceBus(str(_bus_file(tmp_path, "s"))).directives_for(subject)[0]
    assert directive["compute_budget"] == so._DEFAULT_BUDGET


def test_session_close_frees_registries(home):
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u"})
    so._record({"session_id": "s", "turn_id": "u", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    assert "s" in so._WINDOWS and "s" in so._BUSES

    so._close_session({"session_id": "s"})
    # session over: both registries release it, so a long-lived host doesn't
    # accumulate one materialized bus per session. Reverting the _BUSES pop reds this.
    assert "s" not in so._WINDOWS
    assert "s" not in so._BUSES

    # a late hook for the closed session is dropped, not recorded
    so._record({"session_id": "s", "turn_id": "u", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    assert "s" not in so._WINDOWS


# --- real-dispatch E2E -------------------------------------------------------

def test_e2e_through_real_tool_dispatch(home):
    from hermes_cli import lifecycle
    import model_tools

    session_id, task_id, turn_id = "sess-e2e", "task-1", "turn-1"

    # window opens through the real dispatch chain (invoke_hook -> seam -> observer)
    lifecycle.invoke_hook("pre_llm_call", session_id=session_id, task_id=task_id,
                          turn_id=turn_id)
    # the emitter's gate is genuinely open (this is the A1 point)
    assert lifecycle.has_hook("post_tool_call") is True

    # real emitter: self-gates on has_hook, then invoke_hook -> seam -> observer
    model_tools._emit_post_tool_call_hook(
        function_name="write_file", function_args={"path": "x"},
        result={"ok": True}, session_id=session_id, task_id=task_id,
        turn_id=turn_id, tool_call_id="c1", status="success",
    )
    model_tools._emit_post_tool_call_hook(
        function_name="run_shell", function_args={}, result={"error": "boom"},
        session_id=session_id, task_id=task_id, turn_id=turn_id, tool_call_id="c2",
        status="error", error_type="tool_error", error_message="boom",
    )

    # finalize -> directive
    lifecycle.invoke_hook("on_session_finalize", session_id=session_id)

    subject = so._subject(session_id, turn_id)
    path = _bus_file(home, session_id)
    assert path.exists()

    bus = SalienceBus(str(path))
    facets = sorted(s.facet for s in bus.signals_for(subject))
    assert facets == ["memory", "verification"]        # mutation + tool error
    assert len(bus.directives_for(subject)) == 1
    # replay-on-open raises on a broken chain, so reaching here already means the
    # observer wrote a well-formed multi-entry chain; the assert documents intent.
    assert bus.verify_chain() is True
    assert session_id not in subject                   # hashed on disk


def test_closed_gate_produces_nothing_through_dispatch(monkeypatch, tmp_path):
    # Proves a closed gate ⇒ no window, has_hook False, nothing written. The
    # config-parse side of the kill switch is covered by test_kill_switch_disables.
    _force_gate(monkeypatch, tmp_path, False)
    from hermes_cli import lifecycle
    import model_tools

    lifecycle.invoke_hook("pre_llm_call", session_id="s", task_id="t", turn_id="u")
    assert lifecycle.has_hook("post_tool_call") is False
    model_tools._emit_post_tool_call_hook(
        function_name="write_file", function_args={}, result={"ok": True},
        session_id="s", task_id="t", turn_id="u", status="success",
    )
    lifecycle.invoke_hook("on_session_finalize", session_id="s")

    # nothing opened, nothing written
    assert so._WINDOWS == {}
    assert not (Path(tmp_path) / "salience").exists()
