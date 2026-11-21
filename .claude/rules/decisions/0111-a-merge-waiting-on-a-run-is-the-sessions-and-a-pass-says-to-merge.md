---
title: A merge waiting only on a run is the session's, and the passing run says to merge
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/evidence.py, plugin/bin/evidence-gate
date: 2026-11-21
---

## Decision
**"No test run has ever been observed" on a merge's list is the session's to clear**, as a run
the founder's message stopped already was: the refusal says so and names what makes the run (the
session's next finish, `git push` with the pre-push hook). **A finish whose hand-off would list
only such items runs its suite instead of handing off.** **A passing run, where the founder's word
covers the pull request and nothing else stands in its way, blocks the turn's end once with the
merge to make**: `gh pr merge <n>`, said before the unanswered-block question, which the run
answers. Decisions 0010 and 0014 hold otherwise.

## Why
> а это разве не issue плагина или все как надо?

On 2.20.0's own pull request the founder's `+merge` was refused for want of a run, the refusal
said "tell the founder and stop", and named nothing that makes one; the Stop gate's run after the
turn was stopped by the founder's next message, and a green one wakes nobody, so the word waited
on the founder writing again (#381, #382).

## Rejected
- **A session's own run counting.** Decision 0063's reason stands.
- **Not stopping a run the founder's word waits on.** Decision 0044: their message is read now.

## Cost accepted
One more wake of the session after a green run, said once per refusal or hand-off.
