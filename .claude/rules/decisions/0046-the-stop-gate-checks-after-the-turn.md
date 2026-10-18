---
title: The Stop gate checks after the turn, and the founder can always write
paths: plugin/hooks/hooks.json, plugin/bin/evidence-gate, plugin/lib/claude_bestpractice/hookio.py, plugin/lib/claude_bestpractice/founder.py
date: 2026-10-18
---

## Decision
**Claude Code runs the Stop gate once the turn is over** (`asyncRewake`), so the founder can write
to the session the moment it answers. Everything the gate judged it still judges, the suite
included: a refusal, a question and a pull request's hand-off wake the session with an exit of 2,
and a finish it lets go is let go on the record as before. The run has an hour: nobody waits on it.

**A run stops when the founder writes or the session goes back to work**, whatever woke it: their
message to an idle session, a task's notification, the session's own next step. A line stamped
before the run began is the turn's own. Stopped, a run is no verdict, as decision 0044 records it.

This retires 0044's first rejection.

## Why
> эта долгая проверка после ответа клода висит и я не могу ему ничего написать

Decision 0044 stopped the run for a message the founder typed while it ran. Their client takes no
message while a hook holds the turn, so there was nothing to hear, and they waited the suite out
after every answer. Measured on Claude Code 2.1.302: an `asyncRewake` Stop hook lets the turn end
at once, and its exit of 2 wakes the session with its stderr even when the session is idle.

## Rejected
- **A thirteenth hook entry: the checks held in the turn, the suite after it.** The budget is
  twelve, and the two `pr-opened` entries are two because `if` holds one rule.
- **A hook moving itself to the background with `{"async": true}`.** Measured to work, documented
  nowhere, and it wakes nobody.
- **Telling the session at its next turn.** A red suite would wait for the founder to write.
- **Telling a `claude -p` apart to keep its gate in the turn.** It inherits the entrypoint and the
  session id of the session that started it.

## Cost accepted
A `claude -p` whose process ends with its turn stops this gate with it: its work is judged where it
lands, by the green a merge needs. The founder can read "done" before the gate refuses it, and then
sees the session take the work up again.
