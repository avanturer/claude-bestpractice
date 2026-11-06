---
title: A stage the ratchet holds is not probed on every tool call for what cannot move it
paths: plugin/lib/claude_bestpractice/stage.py
date: 2026-11-06
---

## Decision
**The gates resolve the stage on every tool call without the scans that could not change it.**
Held at revenue, nothing is probed. Held at traction or above, the migrations are not read for
a users table, which only ever makes traction. No gate counts commits; `claude-bp status`, which
shows the count, still does. A file the probe reads is read up to its limit, never whole and
then cut. What the stage resolves to is unchanged: the ratchet held it there already.

## Why
> изучи все еще раз точно пойми полностью и составь список что по твоему взвешенному мнению
> реально даст пользу и начнем все по порядку в одной сессии делать

`pre-tool` resolves the stage on every gated call. A Django project's migrations say
`CreateModel` and an Alembic one's `create_table(`, so the pattern never matched and up to two
hundred migrations were read, each whole, on every Bash, Edit and Write; and a `git rev-list
--count` ran for a number only one test read.

## Rejected
- **Caching the probe between calls.** Its signals live in the working tree, which any call may
  have just changed; skipping what cannot move a held stage needs no staleness rule at all.
- **Lazy imports in `pre-tool`.** Measured: the modules every call needs take 69 of the 94 ms
  the whole set does, so the refactor would save about 25 ms of a gate that must fail closed.

## Cost accepted
`claude-bp status` names the ratchet, not the users table, as the reason for a held traction.
