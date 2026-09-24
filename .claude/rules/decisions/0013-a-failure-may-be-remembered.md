---
title: A failure may be remembered; a pass may never be
paths: plugin/lib/claude_bestpractice/evidence.py
date: 2026-09-12
---

## Decision
The gate keeps no cache of a passing run and never will. It does re-assert a FAILURE it has
already observed: when the red record names this suite and the tree it judged hashes to what
is here now, the Stop gate returns that failure instead of running the suite again.

Bounded four ways. A tree carrying uncommitted work hashes to nothing and never matches;
the record must name the same suite; it must have been reached by this version of the gate;
and after an hour the suite runs again whatever the record says.

## Why
The asymmetry is the whole argument. A remembered pass answers "the tests pass" without the
tests having passed — the one thing that must stay impossible here, and the defect class that
made the old result cache the richest source of bugs in this file. A remembered failure can
only refuse a finish that was already refused, on content nobody has touched since it was
refused for it. It has no direction in which it can be generous.

What it costs to lack it was measured: the same suite, the same failure, four full runs,
fourteen minutes of wall clock, on a branch this plugin had told to report and stop (#206).

## Rejected
- **Caching the pass as well.** Every way of keying it is a way of forging a green.
- **Keying on the command instead of the tree.** The command is written by the party being
  gated; `HEAD^{tree}` is written by git.
- **Holding the record until something clears it.** A failure caused by the environment —
  a database another tree was writing to — would then be permanent. The hour is what makes
  a fixed environment rediscoverable.
- **Trusting the record across a dirty tree.** Uncommitted content is not in the tree at
  all, so no hash can stand for it.
- **Trusting it across an upgrade.** The same tree judged by a different gate can get a
  different answer: v1.69.2 fixed a false red and went on repeating it (#234).

## Cost accepted
A suite fixed by something OUTSIDE the tree — a database, an installed package — stays
refused for up to an hour on an unchanged tree. Editing anything clears it immediately.
