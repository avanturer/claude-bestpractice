# Glossary

Definitions only. One line each: meaning, canonical identifier, banned synonyms.

Gate — an executable that the harness runs at a lifecycle event. Code: `plugin/bin/`. Not: hook script, guard, check.
Binding — enforced outside the model, cannot be ignored. Not: strict, strong, hard.
Advisory — delivered to the model, which may ignore it. Not: soft, suggested, optional.
Tier A — committed state, one file per artifact. Code: `store.tier_a`. Not: durable store.
Tier B — ephemeral state in the git common dir. Code: `store.tier_b`. Not: cache, scratch.
Board — the injected view of other live sessions. Code: `board.render`. Not: status, dashboard.
Baseline — the commit a session's diff is measured from. Code: `baseline_commit`. Not: base, start.
Lease — a session's claim on a path IN ITS OWN WORKTREE. Code: `sessions.acquire_lease`. Not: lock, mutex.
Evidence — a fresh passing machine-readable test artifact. Code: `Artifact`. Not: proof, result.
Anchor — an entity's canonical identifier plus its file. Code: `anchor_resolves`. Not: reference.
Stage — the repository's inferred maturity. Code: `stage.current`. Not: phase, level, maturity.
Drift — files changed beyond those the task named. Code: `scope_drift`. Not: creep, spill.
