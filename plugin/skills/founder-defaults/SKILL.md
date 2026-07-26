---
name: founder-defaults
description: This repository's standing engineering defaults: style, structure, testing, security, dependencies. Use at the start of any implementation task.
---

# Standing defaults

Every choice below is already made. Making it again per task is how a codebase ends up
with three date libraries and four error-handling styles, none of which is wrong on its
own. Deviate when the task genuinely requires it, and say why in the code.

Anything a gate already enforces is marked **[enforced]** — those are not advice, and
arguing with them costs a blocked turn.

## Language and style

- **Formatter, never hand-formatting.** Prettier for JS/TS, Black or Ruff-format for
  Python, gofmt, rustfmt. Run it; do not discuss it.
- **Types at every boundary.** Function signatures, exported values, API payloads. Not
  `any`, not `dict`, not untyped JSON crossing a module edge. **[enforced for
  docstrings]**
- **Errors are values or exceptions, consistently, never both in one layer.** Pick per
  language: exceptions in Python, `Result`/tagged unions in Rust and TS where the
  codebase already does, `error` returns in Go.
- **No swallowed exceptions.** **[enforced, permanent budget zero]** Catch what you can
  act on; let the rest travel.
- **Names say what, not how.** `charge_in_minor_units`, not `process_data_v2`.
- **A file is one idea.** When you cannot name it in three words it is two files.

## Comments and docstrings **[enforced]**

Full rules in the `llm-first-code` skill. The short version: a comment carries what the
code cannot — why this and not the obvious alternative, what breaks if it changes, which
measurement drove it. `Args:` / `Returns:` / `:param:` are banned; the types say it.

## Structure

- **Feature-first, not layer-first.** `billing/` containing its routes, logic and tests
  beats `controllers/ services/ models/` split three ways across the tree.
- **Depend inward.** Domain logic imports nothing from the framework. A test for it
  needs no server, no database and no network.
- **One place to read configuration**, at startup, typed and validated. Never
  `process.env.X` scattered through the code.
- **Migrations are additive.** Expand, backfill, contract — never a destructive change in
  one step. **[enforced past prototype]**

## Testing

- **Test the behaviour a user notices**, not the shape of the implementation. A test that
  breaks on a rename is a maintenance cost with no coverage.
- **Every bug gets a failing test first.** It is the only way to know the fix works and
  the only way it stays fixed.
- **No test asserting a mock was called.** That asserts the code you just wrote calls the
  code you just wrote.
- **Skipped tests are not passing tests.** **[enforced]** A suite that executes nothing
  is refused at Stop.
- **The suite runs in under two minutes**, or it will not be run.

## Security

- **Secrets come from the environment.** **[enforced before the write]** Never a literal,
  never a fallback default, never in a comment.
- **Every external input is untrusted**, including your own API's responses, webhook
  payloads and anything an error tracker reports.
- **Parameterised queries only.** String-built SQL is refused on sight.
- **Deny by default in auth.** A missing rule means no access, not full access.

## Dependencies

- **A new dependency needs a comparison on record.** **[enforced]** The stdlib option is
  always one of the alternatives, and it wins more often than it is chosen.
- **No dependency for something under twenty lines** you could own, test and understand.
- **Pin exact versions in applications**, ranges in libraries.
- **Prefer the boring one.** The library with five years of history and no releases this
  month is a feature, not a warning.

## Performance

- **Measure before optimising, and record the number.** An optimisation with no
  before-and-after is a guess with extra steps.
- **The three that actually matter:** an N+1 query, an unbounded result set, and work
  inside a loop that belongs outside it. Everything else is noise until measured.
- **No cache without an invalidation story** written down.

## Git **[mostly enforced]**

Worktree per session, never the trunk, conventional commit messages, no conflict markers
— all enforced. The parts that are not: keep a commit to one logical change, and write
the body when the subject cannot carry the reason.

## When these conflict with the task

The task wins, and you say so in the code: one comment naming the default and why this
case is different. Silent deviation is the thing this file exists to prevent — the next
session cannot tell a considered exception from an accident.
