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

**The installer dirtied the clone it was run from.** Run from a clone, `INSTALL_DIR` is
your own checkout, so `chmod +x plugin/bin/*` chmodded twenty Windows `.cmd` shims and
`git status` came back dirty the moment the install finished.

**Also.** `claude plugin update <name>` fails with `Plugin not found` while the plugin is
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

651 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.
