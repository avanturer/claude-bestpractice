---
title: A merge is judged on the commit it takes, and what the session clears is not put to the founder
paths: plugin/lib/claude_bestpractice/delivery.py, plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/pullrequest.py
date: 2026-11-12
---

## Decision
**A merge is judged on the commit it takes**: the pushed head, `origin/<branch>`, else the
branch. The repository's suite passing on that commit's tree stands for it, whatever ran or was
stopped since on a tree with more in it: a run the founder's message stopped holds it no longer,
and uncommitted files outside the pull request are no part of it. Uncommitted edits to its own
files still hold it, and so does a failure. Without such a pass the tree is all the evidence
there is, as before, and the refusal names the way to one: stash, push, pop.

**A branch's green keeps the last eight clean trees its suite passed on**, so a pass beside a new
file, stamped as no tree, does not take the pushed commit's away.

**A merge refusal whose every item the session clears is the session's**, not the founder's.
**What a handed-off pull request shows as blocking it is judged again** at each Stop on its
branch, at each merge, and once after the upgrade (repair 0048).

## Why
> Files that are not in the PR's pushed head do not block merging that head. A green pre-push
> run of the exact pushed commit counts as "passed on this work".

The founder said `+merge`; the gate refused over a new file for the next pull request and a Stop
run their message had stopped; the session reported, they wrote again, and that stopped the next
run (#309). The pre-push hook had passed the pushed commit and stamped its tree.

## Rejected
- **Ignoring uncommitted files at a merge.** A committed test importing an uncommitted module is
  green here and red on the trunk (decision 0002).
- **Setting a failure aside the same way.** A failure is a verdict; a stopped run is none (0044).
- **`claude-bp verify` running the suite for a stopped run.** Decision 0063's reason stands.

## Cost accepted
A commit nothing passed on clean still needs a run of its own, which a stray file makes the
session stash for. A push to the branch from another clone, not fetched here, is judged as the
commit this clone last saw.
