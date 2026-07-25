# founder-os

A personal Claude Code control plane for building startups with 3–8 parallel agent sessions on one repository.

> **Working name.** The marketplace slug is immutable after publication — renaming breaks every
> existing install, and a top-level `renames` map is the only migration path. Choose the final
> name before the first publish, not after.

## What problem this solves

One person builds a product almost entirely through Claude Code. Several sessions run at once, in
separate worktrees. Nobody reads the diffs. The failure modes that follow are not opinions — they
are measured:

| Failure | Measured |
|---|---|
| The agent says done, the code does not work | Claude 4.5 Sonnet: **0.97** submit rate vs **0.65** test-verified resolve. Two different guard prompts moved it by **zero** |
| The agent edits correct code it was not asked to touch | **60–90 %** of runs across four frontier models, when abstaining was the correct action |
| Rules decay as the session grows | 0 % violation with the policy in context → **30 %** after one compaction → **78 %** after four |
| Too many rules collapse compliance | Perfect-response rate **93.8 %** at 10 rules → **75.0 %** at 20 → **23.8 %** at 40 → **0 %** at 80 |
| Stale context is worse than no context | Stale-only retrieval induced dead-API calls on **15/17** samples; no retrieval produced **0/17** |

Full citations in [`docs/EVIDENCE.md`](docs/EVIDENCE.md).

## The one-sentence design

**Nothing that matters is asked of the model.** Every rule that must hold is enforced by the harness
or by git; the model's context carries only the handful of things no program can check.

## Architecture in three layers

```
policy/    managed-settings.json   root-owned, highest precedence   → the rules that must hold
plugin/    hooks, skills, agents   the delivery vehicle             → behaviour and UX
repo/      git hooks, CI           binds everyone, plugin or not    → the last line of defence
```

A plugin's own `settings.json` honours only `agent` and `subagentStatusLine`, so a plugin can never
ship policy. Anyone who builds this as "just a plugin" ships rules that evaporate the first time the
agent writes a settings file.

## Documents

| Document | Contents |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | Thesis, architecture, the memory model, parallel-session substrate, verification |
| [`docs/ENFORCEMENT.md`](docs/ENFORCEMENT.md) | Binding vs advisory ladder, the ten-rule budget, bypass closures, what cannot be enforced |
| [`docs/ECONOMICS.md`](docs/ECONOMICS.md) | Token budget, prompt-cache invariants, rate-limit admission control |
| [`docs/EVIDENCE.md`](docs/EVIDENCE.md) | Every measured claim with its source and evidence tier |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Build order: V1, V2, V3, and the explicit never-build list |

## Status

Design complete, implementation not started. Nothing in `plugin/` or `policy/` is functional yet —
the manifests are structural placeholders that encode the decisions in the documents above.

## Honest limits

Four things this cannot enforce, stated up front rather than discovered later:

1. **Test semantics.** No matcher distinguishes a justified `skip` from a cheat.
2. **Taste.** No matcher distinguishes good design from bad.
3. **`claude --bare`.** Bare mode drops managed hooks and plugin hooks alike. Nothing in-product
   covers it — which is why the repo layer exists.
4. **A human with sudo.** By design. The threat model is *the agent must not do this by accident*,
   not *the owner must be prevented*.
