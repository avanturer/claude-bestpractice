---
title: A pull request is owed for what only the branch carries
paths: plugin/lib/claude_bestpractice/delivery.py, plugin/lib/claude_bestpractice/pullrequest.py
date: 2026-10-16
---

## Decision
**The Stop gate asks for a pull request only for work no other place delivers.** A commit whose
change the trunk already has as a patch, or that another remote branch carries, is not the
branch's to open one for: decision 0030, applied to the demand. What is left counts only where
the files it touched differ from the trunk, because a squash of several commits is no one
commit's patch. A merge counts for what `--cc` shows of it, the hunks where it differs from
every parent, and a root commit for every file it holds.

**Anything git cannot answer keeps the demand,** as before this was asked.

## Why
> Ветки A, B, C — у каждой свой PR. PR A и PR B влиты в main через squash, PR C открыт.
> Локальная ветка `qa` = merge A + merge B + коммит C.

The gate counted every commit on `main..qa` and ordered a pull request for `qa`. Opened as
told, it duplicates all three (#274). Counted by name, a clean merge of A and C editing one
`CHANGELOG` was such a file, and the demand came back.

## Rejected
- **Asking GitHub which pull request holds each commit.** A network call per commit, at a Stop.
- **Content alone.** A trunk that changed a file again after the squash differs from the branch
  for a reason that is not the branch's.
- **Patch identity alone.** A squash of several commits matches none of them.
- **`--remerge-diff`.** Walked with other commits on git 2.43, a clean merge listed the file the
  commit before it changed, and `diff-tree` stops on it with an internal error.

## Cost accepted
A commit the trunk holds only inside a larger squash, in a file the trunk has changed since,
still asks for a pull request. A commit whose patch the trunk landed and then reverted counts as
delivered.
