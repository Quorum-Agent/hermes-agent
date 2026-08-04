"""Unit tests for `_resolve_watcher_identity` — the per-call notify-target
override that replaces os.environ-based cross-session notification routing."""
from tools.terminal_tool import _resolve_watcher_identity

_ENV = {
    "HERMES_SESSION_PLATFORM": "telegram",
    "HERMES_SESSION_CHAT_ID": "chat-self",
    "HERMES_SESSION_THREAD_ID": "thr-self",
    "HERMES_SESSION_USER_ID": "user-self",
    "HERMES_SESSION_USER_NAME": "self",
    "HERMES_SESSION_MESSAGE_ID": "msg-self",
}


def _fake_gse(env, default=""):
    return _ENV.get(env, default)


def test_no_target_uses_env_and_fallback_session_key():
    ident = _resolve_watcher_identity(None, _fake_gse, fallback_session_key="sess-self")
    assert ident["platform"] == "telegram"
    assert ident["chat_id"] == "chat-self"
    assert ident["thread_id"] == "thr-self"
    assert ident["session_key"] == "sess-self"


def test_explicit_target_wins_over_env_and_never_reads_it():
    target = {
        "platform": "discord", "chat_id": "chat-peer", "thread_id": "thr-peer",
        "user_id": "user-peer", "user_name": "peer", "message_id": "msg-peer",
        "session_key": "sess-peer",
    }
    ident = _resolve_watcher_identity(target, _fake_gse, fallback_session_key="sess-self")
    assert ident["platform"] == "discord"          # NOT telegram from env
    assert ident["chat_id"] == "chat-peer"
    assert ident["session_key"] == "sess-peer"     # peer, not self
    # the ambient (self) identity must not leak into a peer-targeted launch
    assert "self" not in str(ident)


def test_partial_target_blanks_missing_fields_but_session_key_falls_back():
    target = {"platform": "discord", "chat_id": "chat-peer"}
    ident = _resolve_watcher_identity(target, _fake_gse, fallback_session_key="sess-self")
    assert ident["platform"] == "discord"
    assert ident["thread_id"] == ""                # missing -> blank, NOT pulled from env
    assert ident["session_key"] == "sess-self"     # no target session_key -> fallback


def test_empty_dict_is_treated_as_no_target():
    # A falsy dict routes to the current session (env) — a caller cannot
    # accidentally blank the whole routing identity by passing {}.
    ident = _resolve_watcher_identity({}, _fake_gse, fallback_session_key="sess-self")
    assert ident["platform"] == "telegram"
    assert ident["session_key"] == "sess-self"


def test_non_string_target_values_are_coerced():
    target = {"platform": "discord", "chat_id": 12345, "session_key": None}
    ident = _resolve_watcher_identity(target, _fake_gse, fallback_session_key="sess-self")
    assert ident["chat_id"] == "12345"
    assert ident["session_key"] == "sess-self"     # None -> fallback
