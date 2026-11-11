---
title: A push that writes the trunk is refused, however the line names it
paths: plugin/lib/claude_bestpractice/gitpolicy.py
date: 2026-11-11
---

## Decision
**`git push` that would write the default branch on the remote is refused while `protect_trunk`
is on**: named outright (`origin main`, `HEAD:main`, `+main`, `refs/heads/main`), implied by the
branch the tree stands on, or swept in by `--all` and `--mirror`. A tag, a deletion of another
branch and a push of the session's own branch go through. The refusal names
`claude-bp-ship --pr`, and the switch only the founder sets.

## Why
> точно пойми что лучше в плагине нашем добавить или исправить, для такой разработки,
> мсштабируемости и устойчивости безщ багов

The gate refused a commit on the trunk and a merge without the founder's `+merge`, and let
`git push origin HEAD:main` through from any feature branch: the same write to the trunk, by
the one spelling nothing read. On the founder's clone branch protection is not available on
their GitHub plan, and CI has been off since June, so nothing on the remote refuses it either.

## Rejected
- **Leaving it to branch protection.** It is not there, and a gate that depends on the remote
  holds nothing on a remote that does not.
- **Refusing every push from a tree on the trunk.** A tag pushed from the main checkout is a
  release step, and the release is the founder's `+release`.
- **Asking for `+merge` on such a push.** The word accepts work a pull request showed; a push
  to the trunk shows nothing to accept.

## Cost accepted
A repository worked on the trunk alone, with no pull requests, sets `protect_trunk` off once.
A refspec built in a variable the line does not spell is read as the line spells it.
