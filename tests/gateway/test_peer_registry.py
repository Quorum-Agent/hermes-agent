"""Unit tests for gateway.peer_registry — the self-announcing cross-session
peer registry. Liveness is monkeypatched for deterministic reaping tests, plus
two tests exercise the real pid+start-time liveness helper directly."""
import os
import time

import pytest

from gateway import peer_registry


@pytest.fixture
def reg(tmp_path, monkeypatch):
    # Isolate state.db under a temp home.
    monkeypatch.setattr(peer_registry, "get_hermes_home", lambda: tmp_path)
    return peer_registry


def _alive(monkeypatch, value=True):
    monkeypatch.setattr(peer_registry, "_owner_alive", lambda pid, started: value)


def test_announce_and_list_live(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("sess-a", display_name="A", model="m1",
                 a2a_url="http://127.0.0.1:9900/", capabilities=["research", "code"])
    peers = reg.list_live_peers()
    assert len(peers) == 1
    p = peers[0]
    assert p["session_key"] == "sess-a"
    assert p["display_name"] == "A"
    assert p["a2a_url"] == "http://127.0.0.1:9900/"
    assert p["capabilities"] == ["research", "code"]


def test_dead_owner_is_reaped_on_read(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("sess-dead", a2a_url="http://x/")
    _alive(monkeypatch, False)          # owner process dies
    assert reg.list_live_peers() == []  # reaped
    _alive(monkeypatch, True)           # row was physically deleted, not just hidden
    assert reg.list_live_peers() == []


def test_stale_heartbeat_is_reaped(reg, monkeypatch):
    _alive(monkeypatch, True)           # owner alive the whole time
    reg.announce("sess-wedged", a2a_url="http://x/")
    with reg._transaction() as conn:
        conn.execute("UPDATE peer_registry SET heartbeat_ts=? WHERE session_key=?",
                     (time.time() - (reg.HEARTBEAT_TTL_SECONDS + 100), "sess-wedged"))
    assert reg.list_live_peers() == []  # wedged-but-alive still reaped


def test_heartbeat_refreshes_a_stale_row(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("sess-b", a2a_url="http://x/")
    # Make it stale directly. Do NOT list first — a list read would reap it,
    # and heartbeat (update-only) cannot revive a deleted row.
    with reg._transaction() as conn:
        conn.execute("UPDATE peer_registry SET heartbeat_ts=? WHERE session_key=?",
                     (time.time() - (reg.HEARTBEAT_TTL_SECONDS + 100), "sess-b"))
    reg.heartbeat("sess-b")                     # refresh before any reaping read
    assert len(reg.list_live_peers()) == 1


def test_announce_reregisters_a_reaped_row(reg, monkeypatch):
    # The periodic refresh uses announce() (upsert), so a session whose row was
    # reaped while transiently unreachable self-heals on its next beat.
    _alive(monkeypatch, True)
    reg.announce("sess-h", a2a_url="http://x/", capabilities=["c1"])
    _alive(monkeypatch, False)
    assert reg.list_live_peers() == []          # reaped + deleted
    _alive(monkeypatch, True)
    reg.announce("sess-h", a2a_url="http://x/", capabilities=["c1"])
    assert len(reg.list_live_peers()) == 1       # self-healed


def test_resolve_by_key_and_display_name(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("sess-c", display_name="Researcher", a2a_url="http://x/")
    assert reg.resolve("sess-c")["session_key"] == "sess-c"
    assert reg.resolve("Researcher")["session_key"] == "sess-c"
    assert reg.resolve("nope") is None
    assert reg.resolve("") is None


def test_resolve_by_capability(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("sess-d", a2a_url="http://x/", capabilities=["web_search"])
    reg.announce("sess-e", a2a_url="http://y/", capabilities=["code"])
    hits = reg.resolve_by_capability("web_search")
    assert [h["session_key"] for h in hits] == ["sess-d"]
    assert reg.resolve_by_capability("nope") == []


def test_announce_is_upsert(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("sess-f", a2a_url="http://old/")
    reg.announce("sess-f", a2a_url="http://new/")
    peers = reg.list_live_peers()
    assert len(peers) == 1
    assert peers[0]["a2a_url"] == "http://new/"


def test_deregister(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("sess-g", a2a_url="http://x/")
    reg.deregister("sess-g")
    assert reg.list_live_peers() == []


def test_empty_session_key_is_noop(reg, monkeypatch):
    _alive(monkeypatch, True)
    reg.announce("", a2a_url="http://x/")
    assert reg.list_live_peers() == []


def test_owner_alive_real_current_process(reg):
    # Real liveness helper: the current process must read as alive.
    from gateway.status import get_process_start_time
    pid = os.getpid()
    try:
        start = get_process_start_time(pid)
    except Exception:
        start = None
    assert peer_registry._owner_alive(pid, start) is True


def test_owner_alive_impossible_pid_is_dead(reg):
    assert peer_registry._owner_alive(2_147_483_646, None) is False
    assert peer_registry._owner_alive(0, None) is False
    assert peer_registry._owner_alive(None, None) is False
