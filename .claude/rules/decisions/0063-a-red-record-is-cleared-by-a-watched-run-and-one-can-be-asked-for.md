---
title: A red record is cleared by a run the plugin watches, and one can be asked for
paths: plugin/lib/claude_bestpractice/evidence.py, plugin/bin/claude-bp
date: 2026-11-01
---

## Decision
**A red suite record is cleared only by a run this plugin watches, and `claude-bp verify` makes
one when asked.** It runs, now, the run whose pass clears the record: the founder's command where
it answers for it (0035), the run that went red otherwise. It is recorded as the Stop gate records
its runs, under the same count guards, and the failure remembered for the tree (0013) does not
answer it. It runs only where it can clear: never with nothing on record, never for another
branch's record, never for a suite that is gone or has shed tests. Its status is the record's.

**Every surface that says the suite is red names that run**, or why none can clear it, and says a
run in a session's own shell is not one: the merge refusal, the board, the gate's red verdict.

**The founder's `test_command` answers for a suite the gate only detected**, as it did before the
guess (0012): its pass clears the record of the gate's own run there.

## Why
> Зелёный прогон той же команды снимает запись, как обещает сообщение; либо сообщение называет,
> какой именно прогон считается. И сам гейт запускает тесты командой проекта (`make test`), а не
> голым `pytest` на общей базе.

The gate's own `pytest` in `backend/` met 137 dropped connections on the shared database and was
recorded red. `pytest`, `pytest -n auto` and `make test` then passed 10969 there, and the record
and the merge refusal, "that same command passing clears it", stood over every one (#287).

## Rejected
- **Counting a session's own run.** Its output is the gated party's to write (decisions 0002, 0004).
- **The Stop gate running the founder's command in place of the runner.** Their recipe is a
  session's to edit, and the witness exists because recipes were forged; a finish keeps it.
- **Verify with nothing on record running the repository's suite.** It answered for a plan
  nobody made, and its green let a push skip suites that never ran.
- **A push as the way to ask.** The hook runs `make check` before their command, records the
  repository's suite only, and a `--no-verify` push runs nothing.

## Cost accepted
A flaky suite asked for again until it passes is cleared by that pass; the Stop gate still runs
its own at the next finish. Their `test_command` clears a detected suite's record whatever it
runs, short of fewer tests where its count is read: their word on the whole is taken for a part.
