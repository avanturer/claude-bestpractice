# Changelog

## v1.0.1

Everything below was found the same way: by installing v1.0.0 exactly as the README says
to, on a real machine, and then looking at what was actually there. Nothing here was found
by reading the code, and the test suite was green throughout.

### The gate was never installed on the documented path

`Setup` fires on `--init`. Install the plugin into a repository you already have — which
is what `/plugin install` does, and what the README leads with — and `Setup` never fires.
The result: `claude plugin list` said `✓ enabled`, the board rendered on every session,
the in-session gates fired, and **nothing at all guarded a push**. The headline gate was
off in every repository that did not happen to be created through the plugin.

The first session that finds no `pre-push` hook now installs one and says so once.
`claude-bp-ci off` still removes it, and the removal now persists in Tier B, so the next
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

### The installer dirtied the clone it was run from

Run from a clone, `INSTALL_DIR` is your own checkout, so `chmod +x plugin/bin/*` chmodded
all twenty `.cmd` shims — Windows batch files that nothing on a POSIX machine executes —
and `git status` came back with twenty modified files the moment the install finished.
The chmod is now scoped to the files that need it. It stays, because a clone made under a
umask that drops the executable bit has gates that cannot launch.

### Also

- The hook script no longer carries this project's development history in its comments.
  It lands in your repository; it should read like a tool's output, not like a commit log.
- `tests/test_ci.py` had `unittest.main()` above its last class, so running that file
  directly skipped those tests silently.
- The always-on token figure disagreed with itself in all three READMEs — 329 in the body
  against 332 in the badge. `make budget` says 332.

618 tests, 26 doctor checks, always-on context unchanged at ~332 tokens.

## v1.0.2

Everything in v1.0.1 plus the below, and the below is why this release exists: the v1.0.1
tag was cut one commit before these landed. Installing through the marketplace was never
affected — it tracks the default branch — but a tarball or a `checkout v1.0.1` gets a
plugin whose every documented command is `command not found`.

Found the same way as the rest: by installing on a real machine, and by building a small
project for the purpose and reading what the plugin printed into it.

### Seven commands that did not exist

Reported from a clean `install.sh` on a real machine, and all of one shape: prose naming
a binary or a verb that is not there.

- **The installer's symlink list was inverted.** It linked `claude-bestpractice`, which is
  not in `plugin/bin/` — a dangling symlink — and never linked `claude-bp`, which is, and
  is the dispatcher. Every command in the README, including the ones the installer's own
  closing message prints, was `command not found` after a clean install. The list is now
  derived from `bin/` instead of kept in step by hand, which is what drifted when the
  commands were renamed.
- `status` advised `claude-bestpractice adopt`, `adopt` advised
  `claude-bestpractice adopt --restore`, and argparse announced itself as
  `claude-bestpractice`. All three are `claude-bp`.
- The READMEs documented `claude-bp ci off` and `claude-bp reindex` against a dispatcher
  that accepts four verbs and neither of those. They are `claude-bp-ci` and
  `claude-bp-reindex`.
- The install-comparison table promised `claude-bestpractice` in your own terminal.

A test now scans every command named in prose or printed output and fails if the binary
is not in `plugin/bin/` or the verb is not one the dispatcher accepts. It found the
`claude-bp reindex` one, which was not in the report.

### `hosted CI: no workflow in this repository` was false

Computed from a test for one file — ours. A repository with four workflows of its own was
told it had none, one line under `stage: … CI config present`, so two lines of the same
output contradicted each other and the wrong one sounded certain. It now counts what is
actually in `.github/workflows/` and says so.

### `status` wrote to the working tree

It created `.claude/claude-bestpractice/stage/reached-<stage>.json` and left it untracked,
so looking at the repository dirtied it. The stage ratchet still records — from the gates,
which have a mandate to change state. A command named `status` does not.

### One repository, two names

The header took the label from the worktree directory, so `fuddy` and `fuddy-envfix` read
as two repositories. The state was correctly shared throughout — `repo_key` has always
been the common dir — but the label is now derived from the same place, so every worktree
of a clone agrees.

### The first two lines a fresh install prints

Found by installing into a small project built for the purpose and reading the output.

- `status` opened with `Repair the knowledge layer` on a repository where the layer had
  never been built. Nothing was broken; nothing was there. It also named a third command,
  so one condition produced `claude-bp-knowledge init` on one line, `validate` on the
  next, and the README's own `claude-bp init` on neither. A missing layer now says
  `claude-bp init`.
- `init` listed `entities.yaml` under **derived from your code** over a file whose entire
  content is `No types were central enough to derive automatically`. The file is honest —
  inventing entities is the one thing onboarding must not do — and the summary above it
  was not. Files carrying a question are now listed separately from files carrying an
  answer.

### A green run of somebody else's copy of your package

Found by cloning Flask — 5545 commits, sixteen years of history — installing into it, and
pushing a genuine regression in `src/`. The push went out green with 491 tests passing,
because a `.pth` from an unrelated editable install put a different copy of `flask` first
on `sys.path`. Forcing the worktree onto the path instead produced 24 failures.

The gate ran the suite itself and observed exit 0. It was right about the exit code and
wrong about the tree, which is the one thing this gate exists not to be. The
clean-checkout re-run is the defence for exactly this, but it is gated on stage, and a
library has no deploy target so it classifies as `prototype` — meaning the defence was off
on precisely the repositories most likely to be pip-installed.

A passing run now checks that the package it exercised lives inside this worktree. When it
does not, the finish is `UNVERIFIED` and names the package and where it actually resolved.
One interpreter start per top-level package, on the green path only.

632 tests, 26 doctor checks, always-on context unchanged at ~332 tokens.

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
