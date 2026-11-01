---
title: The word carries to a stack's pull request opened again, and a refusal says what it was used on
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/founder.py, plugin/bin/pre-tool, plugin/bin/pr-opened
date: 2026-11-01
---

## Decision
**A `+merge` that named a pull request covers the one opened again on its branch** where the one
it named was closed, was opened into another branch than this one, and the branch stands at the
commit it stood at when the word was said (`merge_accepted_head`). That is GitHub closing a
stack's pull request when the branch below it is merged and deleted: it is never reopened, and
the same work comes back into another branch. The record keeps the branch a call named as its
base (`into`); the base a merge is judged against stays as it was.

**A merge the word does not cover is told what the word said in this chat was used on**, and
what of this chat's it still names, before anything another chat's word left in the pool.
Another chat's word is the reason given only where this chat has said none. A pull request
opened after the word is said to be, read off the log of words heard.

## Why
> A PR with the same head branch and commit as one covered by the word counts as covered. A
> refusal after the word is used up says it was used up on those PRs, instead of pointing at
> other chats.

One `+merge` for a stack of four ran out on the third, refused as "said to" other chats over
their pull requests. #1121, closed by GitHub when #1120 merged, came back as #1124 on the same
branch and commit, and the word that named #1121 did not cover it (#293).

## Rejected
- **The word covering anything opened again on the branch.** A pull request merged where no hook
  heard it reads as closed too, and so does one the founder closed; one into the same branch is
  either of those, and new to them (#279).
- **Recording the stack's base as the one a merge is judged against.** A base that exists only
  on the remote has no commits over it locally, and every merge into it would be refused.
- **Naming every chat's pool entries in the refusal.** The pool is the clone's; the word was
  said here.

## Cost accepted
A stack's pull request pushed to after the word, or recorded before this version, is put to the
founder again once it is opened anew. What a word was used on is kept for this chat's last eight
merges.
