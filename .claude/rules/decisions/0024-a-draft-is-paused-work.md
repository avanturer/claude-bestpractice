---
title: A draft is paused work, and a missing pull request is asked of GitHub before it is claimed
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/evidence-gate, plugin/bin/claude-bp-ship
date: 2026-09-26
---

## Decision
**A draft pull request is paused work.** It is on the board and in the record, and no gate asks
for it, pushes it toward a merge, or lets a `+merge` said about other work reach it. Marked
ready, it is an ordinary obligation again, with the one demand it is owed. `claude-bp-ship --pr`
opens finished work ready for review, because finished work is not paused.

**The Stop gate asks GitHub before it says a pull request is missing.** Once per branch, only
where it would otherwise demand one, through `gh` and with a deadline. What it finds is recorded
like a pull request a hook saw opened. When `gh` cannot answer, the demand says so and names
the command that would.

## Why
> An open draft PR against `main` counts as the PR, so there is no demand to open another one.
> A draft PR is not pushed toward merge by the gate. The founder asked to pause the work, and
> the project's merge needs independent verification plus `+merge`.

The record knows only what a hook in this clone saw. A draft opened from a terminal got "no pull
request against main", an order to open a second one, and "merge it yourself once the checks
pass": three claims the gate could not hold, the last one against decision 0010 (#239).

## Rejected
- **Only rewording the demand.** It would still fire over a pull request that exists, and the
  founder asked for no demand at all.
- **Asking GitHub on every Stop, or from PreToolUse.** A round trip per tool call is how a gate
  gets switched off. Once per branch, where the other answer is a demand, is the whole budget.
- **Treating a draft as waiting for the founder.** That demand asks for their `+merge`, the same
  push toward a merge that pausing was meant to stop.

## Cost accepted
A draft marked ready on the website rather than through a hook here stays paused on the board
until it is merged or closed. The Stop gate reads GitHub with the founder's own `gh` login.
