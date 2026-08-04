# Live cross-session (N-to-N) agent communication — v1 design

**Status:** DESIGN (no implementation yet). **Build target:** this repo (`quorum-agent`).
**Provenance:** consolidated from a 9-model design review — OpenAI `gpt-5`, Google
`gemini-2.5-pro`, DeepSeek `r1`, Qwen, and Meta Llama (via OpenRouter), plus a
four-lens Claude panel (prior-art, minimalism, failure-adversarial, orchestration).

## 1. Goal

Let live Quorum sessions — desktop windows, TUI tabs, CLI — each potentially running
a **different model** with full memory and context, dispatch tasks to each other as
**persistent peers**. This is explicitly *not* ephemeral spawned subagents. The
motivating case: three windows on three models; instead of one model calling
self-model subagents, it hands a task to a peer session that reads, reasons in its own
window, and returns a result.

## 2. The hard constraint (the design driver)

**A session only ACTS when it is given a TURN.** Nothing external can force a session
to speak unprompted. The only legitimate ways to manufacture a turn in a target:

- a human message;
- an **inbound platform / A2A message** (the gateway processes it as a normal turn);
- a **background-process completion notification** (`notify_on_complete`);
- a **cron job owned by that same session** on its own model.

A mailbox row is inert until something triggers the peer's *next* turn to drain it. A
peer also acts only on its **next** turn — you cannot force it to drop an in-flight
task. This is cooperative-actor semantics: the primary verb is **"tell"** (fire-and-
forget), not **"ask"** (synchronous block).

## 3. Resolved decisions

1. **Registry substrate — a `peer_registry` table in `state.db`** (`get_hermes_home()
   / "state.db"`). *Audit:* `state.db` already has WAL + owner-pid + process-start-time
   liveness + claim/ack (`gateway/delivery_ledger.py`, `tools/async_delegation.py`);
   `hermes_cli/active_sessions.py` is a pid-liveness lease *file* with no heartbeat.
   Co-locating the registry with the mailbox in `state.db` reuses one liveness path
   (`gateway.status.get_process_start_time` / `_pid_exists`).
2. **Primary local lane = async SQLite mailbox; keep A2A on hand** for external /
   cross-vendor peers and quick synchronous queries.
3. **Peer trust = configuration** under the existing `security` block, modeled on the
   dangerous-command approval system.
4. **Broadcast / blackboard = deferred for v1** (N≈3; addressing by name or capability
   suffices). Add later only if peer count grows or a real "any-capable-peer" fan-out
   need appears. This choice does not block adding it later.

## 4. Architecture — two lanes

Both lanes make the recipient a **per-call parameter**; neither mutates process-global
state.

### Lane A — synchronous "ask" (A2A, keep as-is)

`a2a_call(peer, msg)` / `a2a_orchestrate(capability, msg)`. The inbound path is already
correct: `adapter.py do_POST → _prepare_task → security.wrap_inbound → MessageEvent`
(via `run_coroutine_threadsafe`) injects a **real turn** into the live session with full
memory, reply returned to the caller. Use this lane for external/cross-vendor peers and
quick queries to a peer you believe is idle. **Not** the primary path for a peer that
reasons at length — a synchronous call blocks the caller's turn up to
`A2A_REPLY_TIMEOUT` (300 s, `adapter.py:74`).

### Lane B — asynchronous "tell" (the primary Quorum↔Quorum path)

A new `peer_send(target?, capability?, message)` tool — **addressing optional**:

1. **Durably write a mailbox row to `state.db` first** (reuse the delivery-ledger /
   async-delegation row + claim/ack + WAL pattern). The message survives even if no
   trigger ever fires.
2. **Manufacture a turn** in the target via exactly one legitimate channel: a
   lightweight background process launched with `notify_on_complete`, targeted by an
   **explicit `notify_session_key` argument** (see §5), never `os.environ`.
3. If the target has **no live process** (registry liveness says offline), degrade to
   **store-only** — the row waits for the target's next human / inbound / cron turn.
   Never assume forced interruption of an in-flight turn.
4. The addressed session **drains its mailbox rows on its next turn** (the same
   drain-after-turn pattern the completion queue already uses). A **reply is a new row +
   trigger back to the sender** — fire-and-forget, never a blocking return. This
   structurally avoids the actor "ask-cycle" deadlock.

## 5. The `notify_session_key` fix (PR 1 — unanimous, low-risk)

Today a background process reads its completion-notification target from **process-global
env**: `tools/terminal_tool.py:295` —
`session_key = get_session_env("HERMES_SESSION_KEY", "")`. Concurrent sends clobber each
other's identity, and an orchestrator overwrites *its own* routing — the exact footgun
`gateway/session_context.py` introduced ContextVars to avoid.

**Fix:** add an optional explicit `notify_session_key` parameter to the terminal
background-launch API (`_run_background_process` / the `terminal(...)` tool surface).
When provided, it is used verbatim as the completion-delivery target instead of the env
read. Per-call, concurrency-safe, orchestrator-safe. Small and self-contained — no new
subsystems — and independently endorsed by all nine reviewers, so it ships first, ahead
of the mailbox and registry.

## 6. `peer_registry` table (in `state.db`)

| Column | Notes |
|---|---|
| `session_key` | primary key; the per-session identity (`HERMES_SESSION_KEY`) |
| `display_name` | human-facing name (SB1 / ORCH / …) |
| `model` | the model this session runs |
| `a2a_url` | Lane-A endpoint (localhost; bind ephemeral to avoid port collisions) |
| `capabilities` | JSON array, for undesignated routing |
| `pid`, `process_start_time` | liveness key (recycled-pid safe) |
| `heartbeat_ts` | catches wedged-but-alive sessions a pure pid check would miss |
| `created_at` | audit |

- **Write** best-effort on `on_session_start`; refresh `heartbeat_ts` periodically.
- **Reap on read:** prune rows whose pid is dead (`_pid_exists` + `get_process_start_time`,
  recycled-pid safe) **or** whose `heartbeat_ts` exceeds TTL. **Not** `on_session_end`
  alone — a SIGKILL skips the hook by design (`gateway/run.py` documents this).
- `_resolve_peer` / `a2a_call` **fall back** to this table when a name is absent from
  `platforms.a2a.extra.agents` config, making static config an optional override.
- **Registry rows are hints, not trust.** The real call still runs `_fetch_card` +
  `security.is_trusted_peer()`; the worst case of a squatted / stale row is a wasted dial
  or a `401`, never action under someone else's identity.

## 7. `security.peer_trust` config (models the approval system)

A new subsection under the existing `security` block (`config_defaults.py:2146`),
mirroring the dangerous-command model (`approval_mode` + permanently-allowed patterns +
`subagent_auto_approve`):

```yaml
security:
  peer_trust:
    mode: allowlist                 # allowlist | ask | trusted
    trusted_peers: []               # peers granted elevated toolsets (persistent allow-list)
    default_inbound_toolsets: [read, chat]   # what an untrusted-but-authenticated peer may run
```

- `mode: allowlist` (default, security-first): an unknown authenticated peer runs only
  `default_inbound_toolsets`; peers in `trusted_peers` get their advertised/elevated
  toolsets. `ask` prompts on first contact; `trusted` grants any authenticated peer.
- **Enforce `advertised_toolsets` as a runtime tool allow-list** on the inbound
  `MessageEvent` — today `adapter.py` only *advertises* them on the Agent Card; runtime
  enforcement is the gap. "Authenticated" must never silently mean "fully privileged."

## 8. Non-negotiable guardrails (from the review)

- **Per-call target everywhere; no `os.environ` in any async path.** (This is the whole
  point of §5 and the orchestrator-safety goal.)
- **`peer_send` is strictly fire-and-forget** — a reply is a fresh turn-triggering row,
  never a blocking wait inside a turn. Prevents the actor ask-cycle deadlock.
- **Cross-context deadlock guard.** Propagate a **call-chain id** on synchronous
  `a2a_call` so the receiver rejects fast when the caller is already an ancestor of an
  in-flight call (A→B→A), instead of burning the full 300 s timeout. The per-context
  ping-pong cap does **not** catch cross-context cycles.
- **Server-enforced hop budget on `orchestrate`** (receiver-checked, not self-reported):
  cap fan-out depth so a peer answering `orchestrate('*')` with its own `orchestrate('*')`
  can't compound `6^depth`.
- **Untrusted framing on every inbound body** (`security.wrap_inbound`), local siblings
  included — a compromised or hallucinating local peer is still adversarial-shaped text.
  Mailbox writes go **only** through the `peer_send` tool surface, never raw `state.db`
  writes (a raw local write could otherwise force a turn below A2A's auth bar).
- **Confused deputy is the #1 risk** (every reviewer): an inbound turn runs in the live
  session with its full ambient toolset. The runtime tool allow-list (§7) is the
  privilege boundary; the wrap-inbound prefix is only a prompt-level mitigation.
- **Coexistence.** `peer_registry` and the mailbox live in `get_hermes_home()/state.db`
  (already under `QUORUM_HOME`), and the A2A default port (`9900`) plus any ownership
  marker must be verified at a **path/identity boundary, not a raw substring** — per the
  ownership red-team finding already applied to the gateway service-unit work.
- **Crash / stale.** TTL heartbeat + pid liveness on every registry read; mailbox rows
  expire with a visible "undelivered after N min," mirroring the delivery-ledger
  abandoned lifecycle.

## 9. Reuse vs. build

**Reuse:** `state.db` + WAL + pid/start-time liveness + claim/ack
(`delivery_ledger.py`, `async_delegation.py`); the A2A adapter and
`security.wrap_inbound` / `is_trusted_peer` verbatim; the `notify_on_complete` spawn
mechanism; `QUORUM_HOME` isolation.

**Build:** the `notify_session_key` per-call param (PR 1); the `peer_registry` table +
`on_session_start` self-announce + TTL/pid reaping (PR 2); the `peer_send` tool + mailbox
rows + drain-on-turn (PR 3); `security.peer_trust` config + runtime inbound tool
allow-list + call-chain-id / hop-budget guardrails (PR 4).

## 10. Phased PR plan

1. **PR 1 — `notify_session_key` per-call param** (the env-exploit fix). Small,
   self-contained, no new subsystems. Ship first.
2. **PR 2 — `peer_registry` table** + self-announce on start + TTL/pid reaping +
   registry fallback in `_resolve_peer`.
3. **PR 3 — `peer_send` tool** + mailbox rows + drain-on-next-turn + reply-as-new-row.
4. **PR 4 — `security.peer_trust`** config + runtime inbound tool allow-list enforcement +
   call-chain-id / hop-budget guardrails.

Each PR: red-team before merge, wait for CI green, merge (branch protection enforces
green). PRs 1–2 are independently useful even if 3–4 slip.

## 11. What we explicitly rejected

- **A central broker / message bus** (RabbitMQ, Kafka, ZeroMQ, etcd/Consul) — violates
  "no central server / no hidden channel," adds dependencies, and complicates coexistence.
  Raised only by the two weakest reviews; the other seven rejected it for this context.
- **`os.environ` pre-targeting** (the current `notify_on_complete` bus) — the one thing
  unanimously judged broken; §5 replaces it.
- **Synchronous `a2a_call` as the primary path** — correct only for quick, known-idle
  peers; wrong for a peer reasoning at length. Lane B is primary.

## 12. Prior art adopted (not reinvented)

- **Actor model — "tell vs ask" + receiver-driven mailboxes** (Erlang/OTP, Akka). The
  turn constraint *is* cooperative-actor semantics. Strongest cross-panel consensus.
- **TTL / lease liveness** (Kubernetes Lease, SWIM heartbeats) — liveness by recent
  heartbeat + pid, not by a dereg hook a SIGKILL never fires.
- **Confused-deputy / object-capability security** (Hardy 1988) — the mandate to enforce
  toolsets as runtime privilege, not advertisement.
- **Filesystem/DB service discovery** — a self-announcing registry as a discovery hint,
  trust re-decided per call.
