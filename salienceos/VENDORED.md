# Vendored: `salienceos`

This directory is a **verbatim vendored copy** of the SalienceOS judgment system
(the salience control plane), imported so the host (this Quorum-Edition agent, the
Stage-2 **test rig**) can be governed by it. It is not authored here — do not edit it
in this repo.

- **Source repo:** `Dev-Dads/salient-os`
- **Source commit:** `7b3630f3e64298623a07b09646a514867b7f92ac`
- **Vendored:** 2026-08-06
- **Contents:** `verifier/`, `interpreter/`, `control/`, `consumers/` (the full package).

## Why vendored, not a dependency

SalienceOS is **stdlib-only and zero-dependency by construction** (enforced in its own
repo by an AST discipline test). Vendoring keeps that property visible and keeps the
host's sealed venv self-contained — no extra install step, no network, no version skew
at runtime. The tradeoff is manual sync: when the source advances, re-copy the package
and bump the commit hash above.

## How to re-sync

From a checkout of both repos:

```
rm -rf salienceos && cp -r ../salient-os/salienceos ./salienceos
# then update the Source commit hash above to `git -C ../salient-os rev-parse HEAD`
```

Never hand-edit files under `salienceos/` here; changes belong upstream in
`Dev-Dads/salient-os`, reviewed and tested there, then re-vendored.

## What consumes it (this repo)

Only the first-party observer at `hermes_cli/observability/salience_observer.py`
(produce path: real activity → salience signals on a per-session bus) and, in a later
change, the compute-budget consumer. Nothing under `salienceos/` reaches back into host
code.
