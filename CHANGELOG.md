# Changelog

## v1.0.0

First release. What follows is written to be checked rather than believed: every claim
below has a reproduction in the test suite or in the commit that made it.

### What this is

A Claude Code plugin for one person who builds products almost entirely through agents,
runs three to eight sessions at once on one repository, and reads almost none of the
resulting code. It enforces what must hold, keeps parallel sessions aware of each other,
and refuses to accept "done" without evidence.

Python 3.9+ and git. No other dependency, enforced in CI. ~332 tokens of always-on
context against a self-imposed ceiling of 400, and `make check` fails the build over it.

### The one idea

**Nothing that matters is asked of the model.** Every rule that must hold is enforced by
the harness or by git. The Stop gate discards the agent's prose, runs your test suite
itself, and treats its own observed exit code as the evidence — because a file claiming
the tests passed is an assertion with angle brackets, and three separate forgeries
defeated the artifact-reading version of this gate before it was replaced.

### How this release was verified

Six rounds of independent adversarial verification, each one a fresh set of agents whose
instructions were to break the plugin by executing it and who were told explicitly not to
trust the test suite or the doctor.

That instruction earned its place. Across five rounds, **every severe defect was found by
running the software and none by reading it** — while 572 tests reported OK and the
doctor reported "All 25 checks passed" inside the very repositories the verifiers had
just broken. Rounds four and five each found that the previous round's fixes had
introduced new defects.

The defects those rounds found, and this release fixes, include: a green finish certified
over a genuinely red suite by four separate routes; the dead-end ledger never reaching a
session that was not present when the dead end was hit; the work ledger being
per-worktree in a product whose premise is many worktrees; one non-UTF-8 filename
permanently wedging a fail-closed gate; the installer bricking itself on any version
bump; and — found on a verifier's very first task, unprompted — writing the test the gate
demands being counted as scope drift, so correct, tested, passing work was blocked four
times and filed as a permanent failure.

### Known limits, stated rather than discovered

- **The doctor proves the gates work, not that your repository is safe.** It builds a
  throwaway repository and attacks that. "All 25 checks passed" is a statement about this
  software. `claude-bp status` is the one that looks at yours.
- **The two install paths are not equivalent.** `claude plugin install` puts the gates in
  your sessions; only `install.sh` puts the `claude-bestpractice` commands in your own terminal.
  `claude-bp init` installs the pre-push gate, so on the marketplace path you must run
  it from inside a session or the gate stays off.
- **`.claude/claude-bestpractice/` is yours to commit.** State travels with a branch only once
  committed; nothing here commits it for you.
- **This is not for teams.** Every trade-off assumes one owner and no reviewer.
- **The enforcement surface is Claude Code specific.** The portable half would be the
  advisory half, which is the useless half.

### Not included, deliberately

Not a memory engine — the harness stores memory, this curates it. Not a code reviewer —
several first-party review paths exist; pick one. Not a task manager — the native task
system is subsumed and gated, never replaced. No daemon, no vector store, no graph
database, no second model watching the first.
