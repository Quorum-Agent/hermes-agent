"""Unit tests for gateway.peer_mailbox — the durable cross-session message store."""
import time

import pytest

from gateway import peer_mailbox as mb


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "get_hermes_home", lambda: tmp_path)
    return mb


def test_send_then_drain_delivers_once(box):
    rid = box.send("sess-a", "hello", from_session_key="sess-x", from_name="X")
    assert rid
    assert box.pending_count("sess-a") == 1
    got = box.drain("sess-a")
    assert [m["message"] for m in got] == ["hello"]
    assert got[0]["from_name"] == "X"
    assert got[0]["state"] == "delivered"
    # exactly-once: a second drain returns nothing, count is zero
    assert box.drain("sess-a") == []
    assert box.pending_count("sess-a") == 0


def test_targets_are_isolated(box):
    box.send("sess-a", "for-a")
    box.send("sess-b", "for-b")
    assert box.pending_count("sess-a") == 1
    assert box.pending_count("sess-b") == 1
    assert [m["message"] for m in box.drain("sess-a")] == ["for-a"]
    # draining a did not touch b
    assert [m["message"] for m in box.drain("sess-b")] == ["for-b"]


def test_oldest_first_ordering(box):
    box.send("sess-a", "first")
    time.sleep(0.01)
    box.send("sess-a", "second")
    assert [m["message"] for m in box.drain("sess-a")] == ["first", "second"]


def test_expired_pending_is_reaped_not_delivered(box):
    box.send("sess-a", "stale", ttl_seconds=-1)  # already expired
    assert box.pending_count("sess-a") == 0       # reaped on read
    assert box.drain("sess-a") == []              # never delivered


def test_limit_caps_a_single_drain(box):
    for i in range(5):
        box.send("sess-a", f"m{i}")
    first = box.drain("sess-a", limit=2)
    assert [m["message"] for m in first] == ["m0", "m1"]
    rest = box.drain("sess-a")
    assert [m["message"] for m in rest] == ["m2", "m3", "m4"]


def test_purge(box):
    box.send("sess-a", "x")
    box.send("sess-a", "y")
    box.purge("sess-a")
    assert box.pending_count("sess-a") == 0


def test_empty_target_or_message_is_noop(box):
    assert box.send("", "x") is None
    assert box.send("sess-a", "") is None
    assert box.pending_count("sess-a") == 0
    assert box.drain("") == []


def test_pending_cap_refuses_overflow(box, monkeypatch):
    monkeypatch.setattr(mb, "MAX_PENDING_PER_TARGET", 3)
    assert box.send("sess-a", "1")
    assert box.send("sess-a", "2")
    assert box.send("sess-a", "3")
    assert box.send("sess-a", "4") is None       # refused at cap
    assert box.pending_count("sess-a") == 3
    # draining frees capacity again
    box.drain("sess-a")
    assert box.send("sess-a", "5")


def test_delivered_rows_pruned_after_grace(box, monkeypatch):
    box.send("sess-a", "hi")
    box.drain("sess-a")  # -> delivered, delivered_at = now
    # simulate time passing beyond the delivered grace window
    future = time.time() + mb.DELIVERED_GRACE_SECONDS + 100
    monkeypatch.setattr(mb.time, "time", lambda: future)
    # any read reaps the long-delivered row
    box.pending_count("sess-a")
    with mb._transaction() as conn:
        n = conn.execute("SELECT COUNT(*) FROM peer_mailbox").fetchone()[0]
    assert n == 0
