"""Self-announcing peer registry for live cross-session (N-to-N) agent comms.

A live session announces itself — its A2A endpoint, model, and capabilities —
into a ``peer_registry`` table in the shared ``state.db`` so other sessions can
discover and address it without a pasted key file or a central server. See
``docs/quorum/cross-session-communication.md``.

Design invariants:
- **Rows are discovery HINTS, not trust anchors.** A resolved peer is still
  authenticated on the real call at the A2A handshake; the worst case of a
  stale/squatted row is a wasted dial. Nothing here grants privilege.
- **Liveness is proven, not assumed.** A row is live only when its owner
  process still exists (pid + process-start-time, recycled-pid safe) AND its
  heartbeat is within TTL. A SIGKILLed session cannot run its own dereg, so its
  row is reaped on the next read (crash-safe) — never relying on a shutdown
  hook. A wedged-but-alive session is caught by the heartbeat TTL.
- Mirrors the ``state.db`` connection / WAL-fallback / liveness conventions of
  ``gateway/delivery_ledger.py`` so it composes with the mailbox that will live
  in the same file.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# A row is stale once its heartbeat is older than this, even if the pid still
# exists (wedged-but-alive). Must exceed the announcer's heartbeat cadence with
# margin — the A2A watchdog refreshes every 60s (_WATCHDOG_INTERVAL), so 240s
# tolerates a few missed beats before reaping a live peer.
HEARTBEAT_TTL_SECONDS = 240.0

_COLUMNS = (
    "session_key", "display_name", "model", "a2a_url", "capabilities",
    "owner_pid", "owner_started_at", "heartbeat_ts", "created_at",
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

    apply_wal_with_fallback(conn, db_label="state.db (peer_registry)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS peer_registry (
            session_key TEXT PRIMARY KEY,
            display_name TEXT,
            model TEXT,
            a2a_url TEXT,
            capabilities TEXT,
            owner_pid INTEGER,
            owner_started_at INTEGER,
            heartbeat_ts REAL NOT NULL,
            created_at REAL NOT NULL
        )"""
    )


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit/rollback on exit, and ALWAYS close it.

    Same rationale as delivery_ledger._transaction — the plain ``with
    _connect()`` form commits but never closes, leaking WAL/SHM fds.
    """
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _owner_stamp() -> tuple[int, Optional[int]]:
    pid = os.getpid()
    try:
        from gateway.status import get_process_start_time

        return pid, get_process_start_time(pid)
    except Exception:
        return pid, None


def _owner_alive(pid: Any, started_at: Any) -> bool:
    """True when the recorded owning process still exists (pid + start time)."""
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        from gateway.status import get_process_start_time

        current_start = get_process_start_time(pid)
    except Exception:
        current_start = None
    if current_start is None:
        try:
            os.kill(pid, 0)  # windows-footgun: ok — EPERM counts as alive below
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    if started_at is None:
        return True
    try:
        return int(current_start) == int(started_at)
    except (TypeError, ValueError):
        return True


def _row_live(row: Dict[str, Any], now: float) -> bool:
    """A row is live iff its owner is alive AND its heartbeat is within TTL."""
    if not _owner_alive(row.get("owner_pid"), row.get("owner_started_at")):
        return False
    try:
        return (now - float(row.get("heartbeat_ts") or 0)) <= HEARTBEAT_TTL_SECONDS
    except (TypeError, ValueError):
        return False


def _loads_caps(raw: Any) -> List[str]:
    try:
        v = json.loads(raw) if raw else []
    except Exception:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def announce(
    session_key: str,
    *,
    display_name: str = "",
    model: str = "",
    a2a_url: str = "",
    capabilities: Optional[List[str]] = None,
) -> None:
    """Register or refresh THIS session's registry row (best-effort, never raises)."""
    if not session_key:
        return
    pid, started = _owner_stamp()
    now = time.time()
    caps = json.dumps(list(capabilities or []))
    try:
        with _transaction() as conn:
            conn.execute(
                """INSERT INTO peer_registry
                     (session_key, display_name, model, a2a_url, capabilities,
                      owner_pid, owner_started_at, heartbeat_ts, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_key) DO UPDATE SET
                     display_name=excluded.display_name,
                     model=excluded.model,
                     a2a_url=excluded.a2a_url,
                     capabilities=excluded.capabilities,
                     owner_pid=excluded.owner_pid,
                     owner_started_at=excluded.owner_started_at,
                     heartbeat_ts=excluded.heartbeat_ts""",
                (session_key, display_name, model, a2a_url, caps,
                 pid, started, now, now),
            )
    except Exception:
        logger.debug("peer_registry announce failed", exc_info=True)


def heartbeat(session_key: str) -> None:
    """Refresh only the heartbeat for THIS session (cheap; called periodically)."""
    if not session_key:
        return
    try:
        with _transaction() as conn:
            conn.execute(
                "UPDATE peer_registry SET heartbeat_ts=? WHERE session_key=?",
                (time.time(), session_key),
            )
    except Exception:
        logger.debug("peer_registry heartbeat failed", exc_info=True)


def deregister(session_key: str) -> None:
    """Remove THIS session's row on graceful shutdown (crash paths rely on reaping)."""
    if not session_key:
        return
    try:
        with _transaction() as conn:
            conn.execute("DELETE FROM peer_registry WHERE session_key=?", (session_key,))
    except Exception:
        logger.debug("peer_registry deregister failed", exc_info=True)


def list_live_peers() -> List[Dict[str, Any]]:
    """Return live peers, reaping dead/stale rows on read (crash-safe cleanup)."""
    now = time.time()
    live: List[Dict[str, Any]] = []
    dead: List[tuple] = []  # (session_key, observed heartbeat_ts)
    try:
        with _transaction() as conn:
            cur = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM peer_registry")
            rows = [dict(zip(_COLUMNS, r)) for r in cur.fetchall()]
            for row in rows:
                if _row_live(row, now):
                    row["capabilities"] = _loads_caps(row.get("capabilities"))
                    live.append(row)
                else:
                    dead.append((row["session_key"], row.get("heartbeat_ts")))
            # Guarded delete: only reap a row still carrying the stale heartbeat
            # we observed, so a re-announce that bumped heartbeat_ts between the
            # SELECT and here (another process, uncommitted at read time) is not
            # clobbered — that session stays discoverable instead of blinking out
            # for a beat.
            for key, hb in dead:
                conn.execute(
                    "DELETE FROM peer_registry WHERE session_key=? AND heartbeat_ts=?",
                    (key, hb),
                )
    except Exception:
        logger.debug("peer_registry list failed", exc_info=True)
        return []
    return live


def resolve(name_or_key: str) -> Optional[Dict[str, Any]]:
    """Resolve a peer by session_key or display_name among LIVE peers."""
    if not name_or_key:
        return None
    for peer in list_live_peers():
        if peer.get("session_key") == name_or_key or peer.get("display_name") == name_or_key:
            return peer
    return None


def resolve_by_capability(capability: str) -> List[Dict[str, Any]]:
    """Return all LIVE peers advertising ``capability`` (for undesignated routing)."""
    if not capability:
        return []
    return [p for p in list_live_peers() if capability in (p.get("capabilities") or [])]


__all__ = [
    "HEARTBEAT_TTL_SECONDS",
    "announce",
    "heartbeat",
    "deregister",
    "list_live_peers",
    "resolve",
    "resolve_by_capability",
]
