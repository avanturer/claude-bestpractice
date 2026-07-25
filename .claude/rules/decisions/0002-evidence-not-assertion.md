---
title: Completion is accepted on evidence, never on assertion
paths: plugin/bin/evidence-gate
date: 2026-07-25
---

## Decision
The Stop gate discards the agent's prose entirely and accepts completion only on a
machine-readable test artifact that exists, is newer than the newest changed file, and
passes. Past prototype stage it is additionally re-run from a clean checkout of the
committed tree in a separate process.

## Why
Self-report is measured worthless: a 0.97 submit rate against a 0.65 test-verified
resolve rate for the strongest model, and two different guard prompts moved that by
exactly zero. False success collapses from roughly 45-76% to 3% in the one benchmark
domain where the environment verifies state independently — same models, fifteen times
lower, purely because something checked.

## Rejected
- Asking the model to self-verify: measured to change nothing.
- An LLM judge: no configuration across five judges and five prompt strategies beat
  AUROC 0.65, while a plain TF-IDF baseline reached 0.83-0.95.
- Trusting a green suite in the working tree: green there and red on the committed tree
  is the common case, caused by an uncommitted or ignored file.
