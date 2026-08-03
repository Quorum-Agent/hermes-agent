# Upstream maintenance

Quorum Edition tracks `NousResearch/hermes-agent` as the `upstream` Git remote
and publishes only from `Quorum-Agent/hermes-agent`.

For each upstream intake:

1. Fetch upstream and inspect the commit range before merging.
2. Treat provider dispatch, tool dispatch, configuration/state roots, desktop
   bootstrap/update code, and release workflows as load-bearing conflict zones.
3. Search new direct SDK/provider calls and direct tool-registry dispatches;
   route them through the existing physical seams or add a fail-closed check and
   behavior test.
4. Preserve upstream prompt-caching semantics and dependency/action pins.
5. Run Quorum qualification in addition to upstream CI.
6. Record the upstream commit in the Quorum release notes.

Quorum-specific commits should stay conceptually narrow—identity/distribution,
policy engine, dispatch integration, and presentation—so future upstream merges
remain reviewable.
