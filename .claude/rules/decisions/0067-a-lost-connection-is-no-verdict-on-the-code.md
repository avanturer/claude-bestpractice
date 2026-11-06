---
title: A lost connection is no verdict on the code
paths: plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/migrate.py
date: 2026-11-06
---

## Decision
**A run of the gate's own pytest in which every test that broke ERRORed on a connection to a
server, lost or refused, and did again when run on its own, is no verdict on the code.** Nothing
is filed red or green, and the finish is UNVERIFIED, for the founder's `+merge` (decision 0010).
What counts is what the driver or the socket says in the error's message (`DISCONNECTED`): never
the traceback, and never a failed assertion.

**A red record an older gate filed over such a run goes on the upgrade**, read off the reports it
kept of that run (repair 0044).

## Why
> **Expected.** The gate runs the test command the repository documents (`make test`), or does
> not count connection errors in setup as a red suite.

Behind Docker's proxy on WSL the shared database dropped connections. Three runs of one tree broke
1, 150 and 146 times, in different files each time, and the record held a merge the founder had
said `+merge` to, clearable only by a pass on that database (#289). Decision 0034's second run did
not help: it met the same proxy.

## Rejected
- **The project's command in place of the runner.** Decision 0063's rejection stands.
- **Running the errors again until they pass.** A flaky server would be asked for a pass.
- **A green.** Those tests ran nowhere.

## Cost accepted
Code that breaks its own connection, a wrong port or a password, finishes UNVERIFIED rather than
red, and its merge waits on the founder's word instead of a fix.
