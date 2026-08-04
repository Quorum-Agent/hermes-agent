"""Durable mailbox for live cross-session (N-to-N) agent messages.

The async "tell" lane of the cross-session design (see
``docs/quorum/cross-session-communication.md``): a sender writes a durable row
addressed to a peer session; the peer drains its pending rows on its next turn.
The row survives a restart and an offline target — delivery is decoupled from
the turn that triggers it.

Mirrors the ``state.db`` connection / WAL-fallback conventions of
``gateway/delivery_ledger.py`` and ``gateway/peer_registry.py`` so mailbox +
registry + delivery ledger share one file and one liveness model.

This module is the DATA LAYER only. The agent-facing ``peer_send`` / inbox
tools, the turn-trigger nudge, and the peer-trust enforcement land in a later
phase — nothing here manufactures a turn or runs a tool.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Undelivered rows expire after this and are reaped on read with a visible
# terminal state, mirroring the delivery-ledger abandoned lifecycle. Delivered
# rows are pruned after a short grace so a drain is briefly idempotent/auditable.
DEFAULT_TTL_SECONDS = 24 * 3600.0
DELIVERED_GRACE_SECONDS = 3600.0
# Backstop against an unbounded/flooded mailbox — the store refuses rather than
# grow without bound. Proper per-sender rate-limiting lives with the send tool.
MAX_PENDING_PER_TARGET = 500

_COLUMNS = (
    "id", "target_session_key", "from_session_key", "from_name", "capability",
    "message", "state", "created_at", "delivered_at", "expires_at",
)


def _db_path():
    return get_hermes_home() / "state.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        _initialize_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _initialize_schema(conn: sqlite3.Connection) -> None:
    from hermes_state import apply_wal_with_fallback

    apply_wal_with_fallback(conn, db_label="state.db (peer_mailbox)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS peer_mailbox (
            id TEXT PRIMARY KEY,
            target_session_key TEXT NOT NULL,
            from_session_key TEXT,
            from_name TEXT,
            capability TEXT,
            message TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at REAL NOT NULL,
            delivered_at REAL,
            expires_at REAL NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_peer_mailbox_target "
        "ON peer_mailbox (target_session_key, state)"
    )


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _reap(conn: sqlite3.Connection, now: float) -> None:
    """Delete expired-undelivered rows and long-since-delivered rows."""
    conn.execute(
        "DELETE FROM peer_mailbox WHERE state='pending' AND expires_at <= ?",
        (now,),
    )
    conn.execute(
        "DELETE FROM peer_mailbox WHERE state='delivered' AND delivered_at IS NOT NULL "
        "AND delivered_at <= ?",
        (now - DELIVERED_GRACE_SECONDS,),
    )


def send(
    target_session_key: str,
    message: str,
    *,
    from_session_key: str = "",
    from_name: str = "",
    capability: str = "",
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> Optional[str]:
    """Enqueue a durable message for ``target_session_key``. Returns the row id
    (or None on failure / empty target). Never raises."""
    if not target_session_key or not message:
        return None
    now = time.time()
    row_id = uuid.uuid4().hex
    try:
        with _transaction() as conn:
            _reap(conn, now)
            cur = conn.execute(
                "SELECT COUNT(*) FROM peer_mailbox "
                "WHERE target_session_key=? AND state='pending'",
                (target_session_key,),
            )
            if int(cur.fetchone()[0]) >= MAX_PENDING_PER_TARGET:
                logger.warning(
                    "peer_mailbox: target %s at capacity (%d pending) — dropping message",
                    target_session_key, MAX_PENDING_PER_TARGET,
                )
                return None
            conn.execute(
                """INSERT INTO peer_mailbox
                     (id, target_session_key, from_session_key, from_name,
                      capability, message, state, created_at, delivered_at, expires_at)
                   VALUES (?,?,?,?,?,?, 'pending', ?, NULL, ?)""",
                (row_id, target_session_key, from_session_key, from_name,
                 capability, message, now, now + float(ttl_seconds)),
            )
        return row_id
    except Exception:
        logger.debug("peer_mailbox send failed", exc_info=True)
        return None


def pending_count(session_key: str) -> int:
    """Number of non-expired pending messages addressed to ``session_key``."""
    if not session_key:
        return 0
    now = time.time()
    try:
        with _transaction() as conn:
            _reap(conn, now)
            cur = conn.execute(
                "SELECT COUNT(*) FROM peer_mailbox "
                "WHERE target_session_key=? AND state='pending'",
                (session_key,),
            )
            return int(cur.fetchone()[0])
    except Exception:
        logger.debug("peer_mailbox pending_count failed", exc_info=True)
        return 0


def drain(session_key: str, *, limit: int = 50) -> List[Dict[str, Any]]:
    """Return pending messages for ``session_key`` (oldest first) and mark them
    delivered atomically, so a message is surfaced exactly once. Reaps expired
    rows first. Never raises."""
    if not session_key:
        return []
    now = time.time()
    try:
        with _transaction() as conn:
            _reap(conn, now)
            cur = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM peer_mailbox "
                "WHERE target_session_key=? AND state='pending' "
                "ORDER BY created_at ASC LIMIT ?",
                (session_key, int(limit)),
            )
            rows = [dict(zip(_COLUMNS, r)) for r in cur.fetchall()]
            if rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE peer_mailbox SET state='delivered', delivered_at=? "
                    f"WHERE id IN ({placeholders}) AND state='pending'",
                    (now, *ids),
                )
            for r in rows:
                r["state"] = "delivered"
            return rows
    except Exception:
        logger.debug("peer_mailbox drain failed", exc_info=True)
        return []


def purge(session_key: str) -> None:
    """Delete all rows for a session (e.g. on explicit reset). Never raises."""
    if not session_key:
        return
    try:
        with _transaction() as conn:
            conn.execute("DELETE FROM peer_mailbox WHERE target_session_key=?", (session_key,))
    except Exception:
        logger.debug("peer_mailbox purge failed", exc_info=True)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "send",
    "drain",
    "pending_count",
    "purge",
]
