# hermes_routing

Quorum's policy core, ported to Python and embedded in the Hermes agent loop.

Zero-dependency Python **executable specification** for the routing/policy layer
that governs every model call: the request compiler (intent, capabilities,
sensitivity floor, web-grounded taint), the ceiling-constrained route planner,
the orchestrator's `plan / trace / delta / result / error` event contract, and
validated model-output envelope handling.

## Why this exists

The decision (ADR 0003 in `aio-self-hosting-quorum`) is that Quorum's policy core
ports **in** to Hermes's Python agent loop rather than Hermes being rewritten or
the policy running as a voluntary sidecar. A policy layer the agent loop can
bypass is a layer that *will* be bypassed — by subagents, cron, compaction, aux
model calls. This package is the policy layer, ready to be wired into the model
call seam (see Phase B).

`hermes_routing.fixtures` provides a versioned, language-neutral JSON contract
for running the same compiler/planner inputs in TypeScript and Python. It omits
generated identities and normalizes results to camelCase so CI can compare
behavior instead of implementation shape.

## Modules

| Module | Ports | Role |
|---|---|---|
| `types.py` | `types.ts` | Execution-location tiers, `leavesDevice`, `modelReach`, descriptors, plans |
| `policies.py` | `policies.ts` | The five policies as ceiling pairs |
| `policy_copy.py` | `policy-copy.ts` | User-visible reach copy, **computed** from ceilings, never hand-written |
| `envelope.py` | TS envelope contract | Validated answer extraction (JSON / `<quorum-final>`), reasoning withholding |
| `planner.py` | `route-planner.ts` | Ceiling-constrained route selection + degrade-to-scaffold + disclosure |
| `software_taxonomy.py` | `software-taxonomy.ts` | Deterministic software-term vocabulary for intent |
| `safe_text.py` | `safe-text.ts` | Display-safe text (strips bidi/zero-width/controls) |
| `compiler.py` | `request-compiler.ts` | Intent classification, capability derivation, sensitive-data detection |
| `orchestrator.py` | `orchestrator.ts` | Async event generator: plan/trace/delta/result/error |
| `events.py` | event union | Discriminated event types |
| `fixtures.py` | differential harness | Versioned JSON input and normalized golden outcomes |
| `uuid.py` | `uuid.ts` | UUID helper |

## Design invariants (mutation-verified)

- **Offline is not special-cased** — it declares `inferenceCeiling: "device"`;
  an in-process model is treated as `device` because it opens no socket.
- **Sensitive / web-grounded content stays on the device** — a *floor* (equality),
  not the policy ceiling, so a LAN peer or rented box is excluded too.
- **A policy is a ceiling, never a preference** — a cloud model that would win on
  score is refused, never merely outranked.
- **Disclosure follows the plan's reach** — present exactly when `leavesDevice`.
- **User-visible reach copy is derived from the ceilings**, so it cannot contradict
  the value it describes.
- **Unvalidated model bytes are never user-visible** — the orchestrator buffers
  output, validates the public-answer envelope, and only then emits a delta.
- **`cost_controlled` is declared but unavailable** until an atomic usage ledger
  can enforce its rolling spend cap. Both planning and orchestration fail closed.

## Development

```bash
scripts/run_tests.sh hermes_routing/tests/ -q
python -m hermes_routing.fixtures path/to/golden.json
```

The repo's Rule 1 (from `../CLAUDE.md` / the Quorum `CLAUDE.md`) applies here:
tests are only trusted if breaking the source turns them red. Run a mutation by
temporarily deleting a guard, confirm a test fails, then restore.
