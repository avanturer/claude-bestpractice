---
title: A review finding shown to the founder is theirs to merge past
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/reviewrules.py, plugin/bin/pre-tool
date: 2026-10-30
---

## Decision
**A `+merge` sent after a refusal or a hand-off listed review findings takes those findings off
that pull request's list**, as it does an UNVERIFIED finish (decision 0036). Kept for the pull
request, not its commit: a finding is about a file, and a base merge leaves it where they saw
it. A finding the list did not carry is put to them in its turn. `claude-bp dismiss` still
rules one false for good.

**`skipif` with a condition is a guard on the environment, not a skipped test.** Only `skip`,
and `skipif(True)`, are.

## Why
> Merge gate refuses after the founder's +merge on a false skipped-test finding (#283)

A new test guarded by `skipif(not _db_ok(), …)` was flagged, the founder read the refusal and
answered `+merge`, and the same merge was refused with the same list. Only rewriting correct
code to dodge the rule emptied it.

## Rejected
- **Dropping findings from the merge check.** Whether to merge past one is the founder's call.
- **Accepting by commit.** Every base merge before the merge would put it to them again (0023).

## Cost accepted
A real finding the founder merged past stays merged past for that pull request.
