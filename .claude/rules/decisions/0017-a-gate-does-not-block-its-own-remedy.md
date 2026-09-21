---
title: A gate never refuses the act that resolves it
paths: plugin/bin/pre-tool, plugin/bin/claude-bp
date: 2026-09-21
---

## Decision
Where a refusal names what to do about it, that act is exempt from the refusal. The
database-isolation gate therefore allows writing and DELETING this tree's `.env`, which is
what its own message prescribes, and `claude-bp database` — the second half of the same
message — falls back to the project's own `worktree_setup` when the machine has no `psql`.

Entering a state and leaving it are different questions. A gate may hold the entrance; it may
never hold the exit.

## Why
The isolation gate refused every write in a worktree whose `.env` named a neighbour's
database, and the write it refused included `.env` itself. The founder did exactly what the
message said, was refused by the same message, ran the prescribed command and was told there
was no `psql` — on a repository whose backend talks to Postgres through `psycopg`. Deleting
the copy of `.env` after the merge was refused too, so a file holding an API key could not be
cleaned up. The only thing that worked was running the write from outside the tree, which is a
hole and not a remedy (#216).

A gate whose remedy it refuses does not enforce a rule. It teaches the session to route
around it, and the route it teaches is the hole.

## Rejected
- **Exempting the whole tree once a collision is recorded.** That is the gate switching
  itself off on the strength of the thing it is complaining about.
- **Exempting any write that merely mentions `DATABASE_URL`.** The text of a write is the
  gated party's; the path is a fact.
- **Issuing DDL without `psql`.** A plugin that reaches for a driver it inferred is one that
  will one day infer it about production. The project's own line runs instead, and only when
  the founder has written one.
- **Reading the project's test setup to decide whether a collision is real.** A `conftest`
  that makes its own database per run means no collision — but inferring that from somebody's
  test code is a guess about the most consequential thing here. `isolate_databases off` is
  the honest answer and the refusal names it.

## Cost accepted
A session can write `.env` in a colliding tree without resolving the collision. It changes
nothing it could not already change from one directory up, and the next ordinary write is
refused exactly as before.
