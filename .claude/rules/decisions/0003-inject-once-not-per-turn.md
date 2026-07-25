---
title: Inject at session start, never per turn
paths: plugin/bin/prompt-capture
date: 2026-07-25
---

## Decision
All context injection happens at SessionStart. The per-prompt gate records the task
statement to disk and returns silence. Any per-turn emission is capped at 200
characters and dropped above it, with the drop logged.

## Why
Injected context accumulates quadratically. A block emitted every turn is written into
the cache each turn and re-read on every later turn — O(T squared) against O(T) for a
block emitted once. For a 1,000-token block, measured in base-input equivalents: 6,000
once versus 164,000 per-turn at 41 turns, and 11,900 versus 695,000 at 100 turns. The
sessions this is built for are long, which is exactly where the penalty is worst.

## Rejected
- Re-injecting rules every turn to fight compliance decay: the decay is real, but
  post-compaction verbatim re-pinning fixes it at under 0.5% overhead.
- Keeping injected content byte-stable to protect the cache: unnecessary. Hook context
  is appended after the cache breakpoint, so a changing block invalidates nothing.
