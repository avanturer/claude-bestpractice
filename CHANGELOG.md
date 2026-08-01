# Changelog

## v1.0.1

Two fixes, both to the push gate, both found by installing v1.0.0 the way the README tells
you to and then looking at what was actually guarding the repository.

### The gate was never installed on the documented path

`Setup` fires on `--init`. Install the plugin into a repository you already have — which
is what `/plugin install` does, and what the README leads with — and `Setup` never fires.
The result: `claude plugin list` said `✓ enabled`, the board rendered on every session,
the in-session gates fired, and **nothing at all guarded a push**. The headline gate was
off in every repository that did not happen to be created through the plugin.

The first session that finds no `pre-push` hook now installs one and says so once.
`claude-bp ci off` still removes it, and the removal now persists in Tier B, so the next
session does not put it back — an opt-out that has to be repeated is not an opt-out.

Proven by `a plain install arms the push gate`, which starts a real session in an
unguarded repository and then looks at the hook, and fails when `ensure` is stubbed out.

### The gate exited 0 when it could not run your suite

The generated hook baked in the project's detected test command, guarded by `command -v`.
If the runner was gone at push time — different machine, changed PATH, rebuilt venv — the
guard failed and the hook fell through `claude-bp-ci` (not on a marketplace user's shell
PATH), through `claude-bp-doctor` (same), and out through `exit 0`. A project that has a
suite pushed with nothing run, reported as checked.

It now refuses, naming the missing runner and `--no-verify`. A repository with no suite at
all is still allowed through: nothing is being skipped there, and that is a true statement
rather than a swallowed failure.

### Also

- The hook script no longer carries this project's development history in its comments.
  It lands in your repository; it should read like a tool's output, not like a commit log.
- `tests/test_ci.py` had `unittest.main()` above its last class, so running that file
  directly skipped those tests silently.

616 tests, 26 doctor checks, always-on context unchanged at ~332 tokens.

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
