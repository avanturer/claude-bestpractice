---
title: An ERROR a second run does not see is not the code's, and a passing report sends a remembered failure back
paths: plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/evidence.py
date: 2026-10-15
---

## Decision
**Tests that ERROR in the gate's own pytest run are run once more, on their own,** when every
test that broke broke in setup, teardown or collection and pytest ran the rest (exit status 1).
pytest picks them from the run's cache, a directory the gate gave both runs outside the tree.
Those that pass then are named in the verdict and not counted as the code failing. A failed
assertion is never run twice, and neither is a run pytest stopped at a collection error.

**A red run names its first broken test and keeps its reports,** scrubbed, under
`test-reports/<suite>/gate-run/`, out of git and cleared by the suite's next run.

**A report of the suite passing, written after a remembered failure, sends the suite back to
be run.** It clears nothing and passes nothing (decision 0013 holds); it only ends the shortcut.

## Why
> ERROR при подготовке теста, которого нет при повторе, не объявляется падением кода.

37 ERRORs at setup in three files the branch never touched; the same files run straight after
passed 41 of 41, and `make check` on the commit passed 7419. The report was deleted with the
temp directory. Then 166 `psycopg.OperationalError`s were repeated from the record at every Stop
while the project's own run wrote 8228 passed (#266).

## Rejected
- **Running failed assertions again too.** That is asking the code for a different answer.
- **Re-running by node id read out of the JUnit report.** pytest's default report carries no
  file, and its node ids are relative to a rootdir the gate's pinned config moves.
- **Filing such a run UNVERIFIED.** Every test was seen to pass; the founder would be asked to
  accept a finish nothing was missing from.

## Cost accepted
A test that errors only when the whole suite runs, from state another test leaves behind, passes
here. The verdict names it, and the founder sees it there.
