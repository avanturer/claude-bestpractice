---
title: Cross-session state lives in the git common dir
paths: plugin/lib/claude_bestpractice/store.py
date: 2026-07-25
---

## Decision
Ephemeral coordination state goes in `$(git rev-parse --git-common-dir)/claude-bestpractice/`.
Durable state goes in `<repo>/.claude/claude-bestpractice/`, one file per artifact, with the
lifecycle encoded in the directory name so a transition is `git mv`.

## Why
The common dir is the only path that is shared by every worktree of one clone,
invisible to git, surviving branch switches, and dying with the clone. Every other
candidate fails one of those. File-per-artifact is not a style preference — it is merge
behaviour: five worktrees produce five distinct filenames and five clean adds, while
five worktrees against one JSON blob produce five overlapping hunks and five identical
generated ids.

## Rejected
- In-worktree state: clobbering, merge conflicts, and N cold caches.
- `~/.tool/` machine-global: cross-repo collision on basename, and one corrupt database
  takes out every project at once.
- `~/tool/<abs-path>/`: correct for a per-session state machine, but it isolates
  worktrees, which is the exact opposite of what a session registry needs.
- Redis or a graph database: infrastructure the owner has to operate, for state that
  fits in a few kilobytes of JSON.
