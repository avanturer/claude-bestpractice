# Changelog

## v1.0.3

**The one-session-per-working-tree rule was enforced by asking where the session sat, not
where the write landed — so it held in exactly one direction, and the direction it missed
was the unsafe one.** Reported from a real machine, with the table filled in.

### A session in a worktree could write into any other tree

`gitpolicy.violations()` asked `ctx.is_worktree`, a fact about the session's own directory.
A session in the main checkout was refused, correctly. A session in a worktree could write
into the main checkout — `CLAUDE.md` included — or into a sibling session's worktree, and
nothing said a word.

That is verbatim the failure the refusal text warns about, printed by the gate that was
permitting it:

> Several sessions sharing one working tree overwrite each other silently — git does not
> notice, and neither will you.

Leases cover part of the same ground, but only for a file some other session is holding at
that moment. An unheld file went straight through.

### And it refused writes that were nobody's business

The same question, asked the same wrong way, denied a `Write` to `/tmp` because the session
happened to be in the main checkout — including into this plugin's own scratch directory, so
checking the plugin was blocked by the plugin. A gate that fires on things that do not
matter is one an agent learns to route around.

Underneath were two errors in resolving where a write actually goes, one in each direction:

- **An absolute path outside the repository was dropped silently.** `(root / "/tmp/x")` is
  `/tmp/x`, and `.relative_to(root)` raises — the target vanished, so the write went
  unexamined by every rule keyed on what it touches.
- **A relative path was resolved against the wrong base.** `cd /tmp/x && printf > a.py`
  writes `/tmp/x/a.py`; it was read as `<repo>/a.py` and refused as a write to a file the
  command never touched. `cd` was not being read at all.

### What holds now

The decision is made on the **target**, resolved as the shell would resolve it:

| Target | Decision |
|---|---|
| Inside this session's own working tree | the existing rules — worktree, trunk |
| Inside another working tree of this repository | **refused**, naming the tree that owns it |
| Outside every working tree | no opinion — not this gate's business |

Nine cases from the report are asserted end to end through the real hook, in a repository
with a main checkout and two worktrees. One consequence worth naming: a scratch file outside
the repository no longer takes a **lease** either, which had let one session deny another a
`/tmp` path the two of them do not share.

691 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.2

**Scope drift was firing on correct work, and the cause was a path the founder never
typed.** Reported from a real session: eight consecutive blocks on the same change, each
one listing every modified file as out of scope.

### What the IDE opened was becoming the task

Claude Code injects a block into the prompt that the founder did not write:

```
<ide_opened_file>The user opened the file /tmp/readonly/Bash tool output (aeqikl) in the
IDE. This may or may not be related to the current task.</ide_opened_file>
```

It carries a path, and a path is the one thing `prompt-capture` mines a prompt for. So the
task scope became `/tmp/readonly/Bash` — non-empty, and matching nothing in the repository.
Every real file was therefore drift.

The safety valve that should have caught this is `test_empty_task_disables_the_check`, whose
docstring reads *"No captured task is our failure, not the agent's. Do not block on it."* An
injected path walks straight past it, because the scope is not empty — it is wrong. That
distinction is the whole bug.

**Two leaks, not one**, and the second needed no help from the filesystem. `root / "/tmp/x"`
is `/tmp/x` — an absolute token discards the root entirely — and the directory fallback then
accepted any token whose parent existed *anywhere on the machine*, up to and including `/`.
So the closing tag itself, `</ide_opened_file>`, was extracted as the path `/ide_opened_file`
and kept, in every session where such a block appeared. Both reproduced before fixing.

Three things now hold, each with a test:

- **Envelope blocks are not the task.** `ide_opened_file`, `ide_selection` and
  `system-reminder` are stripped before capture — including an unclosed opener, which would
  otherwise leave a path-shaped tag behind. Stripped **by name**, never by angle bracket: a
  founder pasting XML is asking for it to be read.
- **A task path must be inside the worktree.** These paths are compared against
  repository-relative filenames, so one that is not in the repository cannot match anything
  and turns the check into "all of it is drift".
- **An IDE block naming a real repository file is still not the task.** Containment alone
  would not catch that one — the file exists and passes every test for a genuine path — and
  it would silently redefine scope to whatever the founder happened to have open.

### The task no longer goes stale

It was captured once and never again, so a session that had long since moved on was still
being measured against its opening line, and every refusal quoted it back. The statement now
follows the founder. Paths **accumulate** where the statement replaces: a later instruction
naming more files genuinely widens what is in scope, and dropping the earlier ones would turn
the files first asked for into drift. Bounded at 64.

### A refusal that named a remedy which does nothing

The message said *"Revert what is out of scope, or state why it was necessary"* two lines
above *"Your description of what you did is not evidence and was not read"*. Nothing reads
prose here — that is decision 0002 — so an agent whose work was correct and whose scope
reading was wrong had exactly one available move: revert correct work. The message now names
the remedies that exist, including the config key that switches the check off.

For the record, the gate was not only wrong: in the same session it caught a real defect —
`make test` in a worktree running against `main`'s code rather than the branch's.

685 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.1

**If you installed v1.0.0, this is the release that can actually reach you — and finding
out why is what this release is.**

### The version string is the update key

`claude plugin update` compares the installed version against the marketplace's and stops
there. It does not look at the code. Measured against the real CLI rather than inferred
from its help text: a local marketplace, an install, a changed file with the version left
alone, then

```
$ claude plugin update claude-bestpractice@claude-bestpractice
claude-bestpractice is already at the latest version (1.0.0).
```

The changed file never reached the cache. Twenty-one commits of fixes — every defect listed
under v1.0.0 below — sat behind that line, and there is **no observable difference between
"up to date" and "permanently stranded"**: both print a tick and exit 0. Running update
again, restarting, re-adding the marketplace all report success and change nothing.

Bumping the version and repeating the experiment fetched the change immediately, with no
marketplace refresh needed. So:

- **This release bumps to 1.0.1**, which is what makes every v1.0.0 fix reachable.
- **`tools/check_shipped.py` now fails the build** when anything under `plugin/` differs
  from the default branch and the version does not. It names the changed files and the five
  places the version lives. Scoped to `plugin/` deliberately — that is exactly the tree the
  marketplace copies, confirmed by installing and listing it, so a README change still
  reaches an `install.sh` user by `git pull` and needs no bump. The gate caught its own
  first miss: `git diff` does not see a file that has never been added, and a new module is
  the most consequential thing that can appear under `plugin/`.

### A session can run code that is no longer installed

`claude plugin update` answers `Restart to apply changes.` once and never mentions it
again. The new version is unpacked into a sibling directory, the old one is marked
`.orphaned_at` and left in place, and every session already running keeps executing the old
copy for as long as it lives. Nothing said so. A founder who updates to get a fix and does
not get it had no way to tell which of two things went wrong.

A session that is running a superseded copy now says so on its own board, and
`claude-bp status` says it too. Purely local — the version is the name of the directory the
code is in and the alternatives are its siblings, so this costs no network call and prints
nothing on the sessions that are running what is installed, which is all of them.

### The release cuts itself

This entry is the first release body this repository did not publish by hand, and the
reason is a boundary rather than a preference. An agent session pushes through a git proxy
that answers a tag with:

```
ERR push contains a ref outside refs/heads/*; only branch updates are permitted.
```

Branch updates, nothing else. So every release needed a person at a keyboard, and the
observable consequence was already sitting in this repository: `v1.0.0` pointed at a commit
**twenty-one commits behind** the code its own notes described, because the tag was cut once
and the fixes kept landing.

`.github/workflows/release.yml` moves the tag and the release onto the one event an agent
can cause — a merge to the default branch — and leaves the credentials on GitHub's side
rather than in the session. It reads the version from `plugin/.claude-plugin/plugin.json`,
does nothing if that release exists, and otherwise **runs `make check` before publishing
anything**. That last part is not ceremony: a merge is made through the API, so the pre-push
hook that guarded the branch never saw the commit being released, and a release nobody
executed would be this project's own thesis broken by its own release mechanism.

Unlike `check.yml` it is **not** gated behind `CLAUDE_BESTPRACTICE_CI`. That variable exists
so a repository does not spend metered minutes re-running gates that already ran locally.
The same gate on a release means the release silently never happens, which is the failure
class this project is written against. It runs only when the version changed.

The notes come from this file, matched on the exact heading — so `1.0.1` is never answered
by `## v1.0.10`, and a version with no entry is a **refusal**, not an empty release body. A
test asserts the current version has notes, one merge before the workflow would have to.

**Its first run refused to publish, and it was right.** `make check` failed on the runner
with three failures that pass on every developer machine. `check.yml` is gated behind
`CLAUDE_BESTPRACTICE_CI` and had therefore never executed, so this was the first time
anything ran the suite on a clean machine — and three tests build a throwaway Python project
and require the gate to *actually execute pytest over it*. A bare runner has no pytest, the
gate correctly declines to witness anything, and those three fail. Reproduced locally by
blocking the import rather than guessed at.

Both workflows now install a test runner, and the assertion that forbade it is narrowed
rather than deleted. Its reason — "the stdlib-only constraint is void if CI quietly
pip-installs the difference" — turned out not to describe the enforcement:
`tools/check_stdlib_only.py` reads the source, so it refuses `import requests` under
`plugin/` whether or not requests is installed. Verified by adding one and watching it fail
with both requests and pytest present. Exactly one install is permitted now, it is named,
and the plugin may not import it.

### Also

- All three READMEs now have an **Upgrading** section. Two of them had none at all.
- It states the restart, the qualified `name@marketplace` form, that the version is the
  update key, and that an `install.sh` install updates by `git pull` instead.

680 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

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

Rounds of independent adversarial verification, each one a fresh set of agents whose
instructions were to break the plugin by executing it and who were told explicitly not to
trust the test suite or the doctor. Then a final pass that installed the plugin the way
the README says to, on real repositories, and looked at what was actually there.

That instruction earned its place. **Every severe defect was found by running the software
and none by reading it** — while the suite reported OK and the doctor reported all checks
passed inside the very repositories that were broken.

### What verification found, and this release fixes

Listed because the list is the evidence. Each of these was live in a build that was green.

**The push gate was not installed on the documented path.** `Setup` fires on `--init`, so
the pre-push hook reached only repositories created through the plugin. Install into a
repository you already have — which is what `/plugin install` does — and `claude plugin
list` said `✓ enabled` over a push path with nothing on it. The first session that finds
no hook now installs one and says so once; `claude-bp-ci off` removes it and the removal
persists, because an opt-out that has to be repeated is not an opt-out.

**The push gate exited 0 when it could not run your suite.** The baked test command was
guarded by `command -v`; when that guard failed the hook fell through every fallback and
out through `exit 0`. A project that *has* a suite pushed with nothing run, reported as
checked. It now refuses and names the missing runner. A repository with no suite at all is
still allowed through — nothing is being skipped there.

**A green run of somebody else's copy of your package.** Found on a clone of Flask, 5545
commits and sixteen years of history: a genuine regression in `src/` pushed green with 491
tests passing, because a `.pth` from an unrelated editable install put a different copy of
`flask` first on `sys.path`. Forcing the worktree onto the path produced 24 failures. The
gate was right about the exit code and wrong about the tree. A passing run now checks that
the package it exercised lives inside this worktree.

**Four parallel sessions were one session, and it fed them each other's work.** The
headline scenario, run for the first time: four live sessions, one per worktree, each told
to change a different file. Two of the four rewrote a file they had never been asked to
touch, reverting their own correct work to do it. Four sessions produced one record —
worktree from the first, branch from the third, task from the second — because `claude`
children inherit `CLAUDE_CODE_SESSION_ID` and identity was keyed on that alone. Identity
is now (harness id, worktree). Re-run: four records, four branches, four correct files.

**Seven commands that did not exist.** The installer's symlink list linked
`claude-bestpractice`, which is not in `plugin/bin/`, and never linked `claude-bp`, which
is and is the dispatcher — so after a clean install every command in the README, including
the ones the installer's own closing message prints, was `command not found`. Five more of
the same shape in printed output and docs. A test now scans every command named in prose
or output against `plugin/bin/` and the dispatcher's verbs; it found one nobody reported.

**Three outputs that were not true.** `hosted CI: no workflow in this repository` was
computed from a test for one file — ours — so a repository with four workflows of its own
was told it had none, one line under `stage: … CI config present`. `status` created a
stage marker in the working tree and left it untracked, so looking at a repository dirtied
it. And one repository read as two because the header took its label from the worktree
directory rather than the git common dir.

**A subagent briefed with a template.** On any repository whose knowledge layer had been
created and not yet answered — every repository for its first hour — a subagent's entire
brief was three lines reading `<ANSWER THIS — …>`. Worse than nothing: it costs tokens,
tells it nothing, and teaches it that the channel carries noise. Unanswered sections are
dropped and an empty brief is not sent; answered ones still go verbatim, which is
load-bearing.

**A dead end about code that had since been rewritten.** The attempts ledger stamps every
record with the blob hashes of the files it was about, and nothing read the stamp. A dead
end recorded against a file since rewritten was still presented as current advice. Marked
rather than suppressed — "we tried X and it failed because Y" stays true whatever happens
to the file, so what is said is that its bearing on the current code may have changed.

**The first two lines a fresh install printed.** `status` opened with `Repair the
knowledge layer` on a repository where the layer had never been built, and named a third
command while doing it. `init` listed `entities.yaml` under **derived from your code** over
a file whose entire content is `No types were central enough to derive automatically`.

**A Ruby project could not push at all.** The fallback that guesses a test command asked
whether a directory named `test` or `tests` existed and concluded Python. Jekyll, gson and
guzzle each have one and none of them is Python, so `python3 -m pytest -q` went into a Ruby
repository's push hook — and pytest exits 5 for "no tests ran", which meant every push out
of that repository was refused, permanently, over a command naming no file in it. Found by
cloning eleven real repositories across six ecosystems, installing into each, and pushing.
A language is now inferred from test files, not from a directory name.

**Nothing ever told a session the knowledge layer was missing.** The layer exists to ask
the founder the three things only they know — what this is, who it is for, its non-goals.
`Setup` fires on `--init` and `claude-bp status` is a command the founder runs, so on the
ordinary install path the question was never asked. Verified: a fresh session on a fresh
repository, told only "get started", went and edited code and left the layer absent. It now
runs `claude-bp init` itself and asks the five questions in plain language.

**`adopt` wrote a dead product name into your own settings.** The quarantine key was
`_founderOsQuarantined`, a name this project shed before it ever shipped, landing in the
founder's `.claude/settings.json` where a reader has no way to tell what wrote it. Found by
running `adopt` against a realistic competing installation for the first time; an earlier
grep for the old name had missed it because the identifier is camelCase. Renamed with no
compatibility path — the rename predates the first release, so no settings file carries the
old key, and this project's own slop gate refused the compat shim when the fix first tried
to add one.

**The installer dirtied the clone it was run from.** Run from a clone, `INSTALL_DIR` is
your own checkout, so `chmod +x plugin/bin/*` chmodded twenty Windows `.cmd` shims and
`git status` came back dirty the moment the install finished.

**Starting a session dirtied your working tree.** `status` was fixed for this and the
gates were not, so `.claude/claude-bestpractice/stage/reached-prototype.json` came back
untracked in every repository that had done nothing but start a session. `prototype` is
the floor, order 0, so that marker could never hold a ratchet — there is nothing below it
to regress to. It was pure residue, and it landed in a repository whose own rules require
`git status` to be clean. Nothing is written at the floor now. A marker above it is real
state, and is yours to commit.

**The board promised a check that would not run.** The line arming the push gate read
"checks now run before every push" in every repository — including one with no `make check`
target and no detectable runner, where the hook reaches `claude-bp-ci` by name, does not
find it on a marketplace user's PATH, and exits 0. A promise larger than the fact is the
exact failure this project is written against, and this was the project making it. The line
now names the command it means — `make check`, or the runner that was detected — or says
plainly that the hook will refuse nothing until this repository has one.

**Also.** `claude plugin marketplace add <owner>/<repo>` resolved to `git@github.com:` on a
machine with no SSH key and stopped there, so the README now gives the HTTPS URL to pass
instead — and says what that does not fix, which is a global `insteadOf` rewrite in your own
git config. `claude plugin update <name>` fails with `Plugin not found` while the plugin is
installed — `update` needs the qualified `name@marketplace` form where `install` does not,
so the README documents the one that works. `reaped.jsonl` was the only structure in Tier B
that never shrank and is now capped. And earlier rounds fixed: a green finish certified
over a genuinely red suite by four separate routes; the work ledger being per-worktree in
a product whose premise is many worktrees; one non-UTF-8 filename permanently wedging a
fail-closed gate; and writing the test the gate demands being counted as scope drift, so
correct, tested, passing work was blocked four times and filed as a permanent failure.

### What was measured, not assumed

- **Four hundred sessions in one repository.** State grows linearly and stays small — 202
  KB — and session start stays flat at 0.19s against a 30s limit. One session start then
  reaped 400 dead records in 1.45s.
- **A sixteen-year-old repository.** Every gate inside its timeout, entity derivation
  finding the real entities with correct file anchors, an existing husky `pre-push`
  symlinked at a tracked script moved rather than written through and chained so its
  refusal still refuses.
- **A real version upgrade.** State written under an older install was still there and
  still readable after — it lives in your repository and in your git common directory,
  never in the plugin cache.
- **Node and Go end to end.** `npm test --silent` and `go test ./...` detected, baked into
  the hook, green push allowed, red push refused.
- **A prompt-injection payload arriving as a production signal.** Quarantined, fenced,
  marked as never an instruction, and the fence not broken by the payload's own backticks.
  Live secrets in the same payload did not survive into the written file.

### Known limits, stated rather than discovered

- **The doctor proves the gates work, not that your repository is safe.** It builds a
  throwaway repository and attacks that. "All 26 checks passed" is a statement about this
  software. `claude-bp status` is the one that looks at yours.
- **Linux only, by evidence.** macOS and Windows have never executed this — not once. The
  twenty `.cmd` shims are untested everywhere. See `docs/LIMITS.md`.
- **A false green is still possible on purpose.** The agent writes your code, your tests,
  your test command and your build files. A `conftest.py` monkeypatch, a test that asserts
  nothing, a runner shadowed on PATH — all still work. This gate raises the cost and leaves
  a record; it does not make forgery impossible. `docs/LIMITS.md` names each attack.
- **The two install paths are not equivalent.** `claude plugin install` puts the gates in
  your sessions; only `install.sh` puts the `claude-bp` commands in your own terminal.
- **`.claude/claude-bestpractice/` is yours to commit.** State travels with a branch only
  once committed; nothing here commits it for you.
- **This is not for teams.** Every trade-off assumes one owner and no reviewer.
- **The enforcement surface is Claude Code specific.** The portable half would be the
  advisory half, which is the useless half.

### Not included, deliberately

Not a memory engine — the harness stores memory, this curates it. Not a code reviewer —
several first-party review paths exist; pick one. Not a task manager — the native task
system is subsumed and gated, never replaced. No daemon, no vector store, no graph
database, no second model watching the first.

655 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.
