---
title: The gate names where the report it reads is written, and git never sees it
paths: plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/worktree.py, plugin/lib/claude_bestpractice/delivery.py
date: 2026-10-08
---

## Decision
**Where the Stop gate reads a report because it cannot witness a run, it names one command that
writes it:** run in the suite's own directory, by the runner the gate drove when that run was
the one stopped, writing `.claude/claude-bestpractice/test-reports/<suite path>/junit.xml`.

**That directory is the plugin's, and git never sees it.** `worktree.hide` excludes it before the
path is first named, so a report there is uncommitted work to no check that asks git.

**The gate reads that path first, then `artifact_globs` in the suite's own directory, then at the
top of the tree.** The newest report wins, as it always has.

**The merge check names the uncommitted paths it refuses over.**

## Why
> Either the gate says where to write the artifact so it does not dirty the tree (an ignored or
> out-of-tree path), or the uncommitted-changes check ignores the artifact it asked for.

A `backend/` suite outlived the Stop hook and the gate asked to "run your test suite with a JUnit
XML reporter": it had read the markers at the top of the tree, so it named no command and no
path. The session wrote a report the repository tracks, and every merge check after it refused
over "there are uncommitted changes" (#261).

## Rejected
- **Out of the tree.** Claude Code's sandbox hands a command a temp directory this hook does not
  see, and `.git` is a path it asks the founder about, or refuses under `dontAsk`.
- **The merge check ignoring what `artifact_globs` match.** `reports/**/*.xml` is source in some
  repositories, and a pull request would open without it.
- **The founder's `.gitignore`.** A tracked file of theirs, for a fact about one clone (0018).

## Cost accepted
A report written anywhere else is still the tree's own content, tracked or not, and the merge
check now says which file it is. Every clone gets one more line in `.git/info/exclude`.
