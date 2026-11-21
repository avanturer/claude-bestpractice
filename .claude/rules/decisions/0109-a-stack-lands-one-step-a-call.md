---
title: A stack lands one step a call, and a deletion that closes its pull request is refused
paths: plugin/lib/claude_bestpractice/stack.py, plugin/bin/claude-bp-ship, plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/pre-tool, plugin/bin/pr-opened
date: 2026-11-21
---

## Decision
**`claude-bp-ship --stack` prints the one command that comes next in landing a stack, and runs
none**: the next pull request moved onto the trunk, then the lowest merged from its own tree
with its branch deleted, then each above rebased off its commits (`--onto`, past the commit
kept in Tier B), pushed with a lease on what GitHub had of it when the rebase was printed, and
the landed tree removed without `--force`. Read off GitHub and git at each call; a branch
GitHub moved since, or one rebased by hand, is not pushed.

**A merge with `--delete-branch`, or a push deleting a branch, is refused while a pull request
recorded here is opened into that branch**, naming `gh pr edit <n> --base <trunk>`. A retarget is
recorded on its PostToolUse. **A typed `git worktree remove` of a tree not the session's is
refused while it holds ignored files nothing else has** (decision 0039).

## Why
> A plugin-owned way to land a stack that the gates accept by construction.

Six stack merges a month went through a project's own command: retarget, rebase, push, `gh pr
merge` in a loop, `worktree remove --force`. The merge gate refuses the loop (decision 0040), and
the forced removal skipped what the plugin keeps a tree for (#366). GitHub closes a pull request
whose base branch is deleted and never reopens it (#293, #329).

## Rejected
- **The command running the merges.** No PostToolUse in its process: the word unspent, the pull
  request unsettled (decision 0038).
- **Refusing any merge of a stack's lowest.** One that keeps its branch closes nothing.

## Cost accepted
One call per step. A retarget made on the website is not recorded, and a deletion after it is
refused once.
