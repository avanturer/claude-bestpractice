---
title: The gate runs the suite; an artifact is not evidence
paths: plugin/lib/founder_os/evidence.py
supersedes: "0002"
date: 2026-07-25
---

## Decision
The Stop gate runs the project's test command itself and treats its own observed exit
code as the evidence. An artifact is read only when no runner is detectable, and the
verdict then says UNBOUND. Re-runs are skipped when a receipt already covers the
identical tree, where identical means blob hashes of the changed files.

This retires 0002's mechanism, not its thesis. Completion is still accepted on evidence
and never on assertion; what changed is that a file claiming tests passed is an assertion.

## Why
Adversarial verification broke the old mechanism three ways, none needing an attacker —
only a model taking the cheapest path to a green gate. A hand-written four-line JUnit
file was accepted while a committed test genuinely failed; an artifact from another
project dated 2019 was accepted; and `touch junit.xml` cleared the freshness check.
Separately, a suite skipped entirely by an ordinary `skipif` on a missing `DATABASE_URL`
reported "2/2 passed" over a run that executed no assertions.

The root is that the artifact was never bound to an execution. Freshness by mtime is not
binding: `touch` is one command, and the contents are whatever wrote them.

## Rejected
- Signing the artifact: whatever produces the signature is available to whatever writes
  the file. Raises the cost of forgery without changing its possibility.
- Trusting a plausible-looking artifact: every field it is judged on is written by the
  same hand.
- Only the clean-checkout re-run: it covers committed code, so it says nothing about the
  working tree the founder is about to keep.
- Running unconditionally on every Stop: rejected on cost; the content-keyed receipt
  means an unchanged tree pays once.

## Cost accepted
The gate now executes project code. Bounded by the same timeout as the clean re-run, and
it runs the command the project itself declares.
