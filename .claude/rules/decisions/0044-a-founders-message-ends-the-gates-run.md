---
title: A founder's message ends the gate's run of the suite
paths: plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/founder.py, plugin/lib/claude_bestpractice/evidence.py, plugin/bin/evidence-gate
date: 2026-10-17
---

## Decision
**A suite the Stop gate runs stops as soon as the founder types into the session**, and the turn
ends for their message where nothing else stands in its way. The gate reads what the harness
appends to the transcript while it runs: a message of the founder's queued there is enough, and
a task's notification or a message of this plugin's is not.

**A run stopped that way is no verdict.** Nothing is filed red, green or UNVERIFIED, and no note
is asked for. It is recorded as cut short, with its tree, which a merge reads as no green on the
branch until a run passes or fails, the gate's or the pre-push hook's. A green of the whole
suite on the very tree it stopped on still stands: the run was judging what had passed.

## Why
> Сообщения, которые пользователь пишет в это время, стоят в очереди и доходят до модели только
> после конца прогона или после Esc. Пользователь видит, что его «+merge» не читают.

Five minutes of pytest and a minute of jest at a finish while the founder's message waited
(#277). Measured on 2.1.302: a message sent while a Stop hook runs is in the transcript at once,
as a `queue-operation` `enqueue`; the hook read it half a second later, and the message was
delivered only once the hook returned.

## Rejected
- **The suite in an `asyncRewake` hook, after the turn.** A thirteenth hook entry, and a run
  going on while the session works again reads files it is editing, beside its own test run.
- **Letting the run finish in the background once they speak.** The same, one step later.
- **Skipping the run where the pre-push hook passed on the tree.** A remembered pass (0013).
- **Stopping the run and keeping the older green.** A `+merge` typed during it merged on that
  green the change nobody had run.

## Cost accepted
A stopped run starts again from the beginning at the next finish, so a founder who writes
during every run waits for the merge until one is left alone. A message typed before the Stop
began, while the final answer was being written, is not seen.
