---
title: The founder's test command answers for the gate's own run of the suite
paths: plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/config.py
date: 2026-10-15
---

## Decision
**A red record of the gate's own run (`pytest`, `go`) is cleared by the command the founder
declared for that suite passing**: `test_command` in config.json for the repository,
`test_commands` for a path. Only what they wrote: a command detection found is not their word.
The count guards stand: a suite that has shed tests clears nothing.

**The merge refusal names that command beside the recorded one.**

## Why
> зелёный прогон настроенного `test_command` снимает красную запись.

The gate's `pytest` went red while the pre-push hook's `make ci` ran beside it. `make test`, their
`test_command`, then passed 7488, and the merge was still refused over "recorded for `pytest`;
that same command passing clears it": nothing a session runs is `pytest` (#268).

## Rejected
- **Any recipe clearing a witnessed record.** `make check` is not the suite unless the founder
  named it, and a session writes Makefiles.
- **Clearing on the gate's next Stop alone.** On an unchanged tree that waits out the hour of
  decision 0013, and a session told to stop has no next Stop to wait for.

## Cost accepted
Their command's recipe is the session's to edit, so this trusts the recipe as far as a red record
of `make test` itself always was: the declared-count floor is what stands behind it.
