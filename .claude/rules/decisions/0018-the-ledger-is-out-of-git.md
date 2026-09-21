---
title: The ledger lives in the main checkout and out of git
paths: plugin/lib/claude_bestpractice/worktree.py, plugin/lib/claude_bestpractice/plan.py
supersedes: "0001"
date: 2026-09-21
---

## Decision
Task files stay where v1.60.0 put them — one per card, in the main checkout, the tree that
outlives every other — and git does not track them. `worktree.hide` writes the exclude rule
per clone, and an upgrade takes the ones already in the index out of it with `git rm --cached`,
leaving every file on disk and one staged deletion to commit.

This retires 0001's "Tier A is committed by definition" for the LEDGER only. Config, the stage
ratchet and attempts are still committed state and the health line still reports a rule that
hides them.

## Why
Every session in a clone writes cards into the one checkout they all share. Tracked, that
made 67 modified files from a dozen branches sit in the founder's shared tree, which refuses
`git merge --ff-only`, which leaves the tree lagging the trunk, which makes its suite red on
code nobody present wrote, which refuses a session whose work is already merged. One chain,
three dead-ends filed as separate issues, and a quarter of sixty days of commits that were
nothing but this plugin's bookkeeping (#219, #220).

A board is for coordinating the sessions that are live in this clone. Nobody was reading it
in a diff.

## Rejected
- **Moving it to `~/.claude` keyed by the repository path.** The founder's first choice, and
  it loses more: the board dies with the machine rather than the clone, and renaming the
  repository directory orphans it. Untracking gives the same clean tree for none of that.
- **Moving it to Tier B, the git common dir.** Tidier on paper, and it rewrites the ledger's
  home — the most load-bearing module in the plugin — to buy what one exclude rule buys.
- **Leaving it tracked and teaching the gates to ignore it.** The gates already ignore it;
  `git merge --ff-only` does not, and that is what actually stopped the founder.
- **Editing the founder's `.gitignore`.** A tracked file of theirs, for a fact about one
  clone. `.git/info/exclude` is where such facts belong (same reasoning as 0001).
- **`git rm` rather than `--cached`.** Deleting a founder's file to fix an index is not a
  repair.

## Cost accepted
A card can no longer be read in a diff or carried to another machine through git. Stated here
rather than discovered later.
