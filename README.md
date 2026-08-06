<div align="center">

# claude-bestpractice

**Memory, coordination and enforcement for building products with several Claude Code sessions at once.**

[![version](https://img.shields.io/badge/version-1.7.0-black)](https://github.com/avanturer/claude-bestpractice/releases)
[![tests](https://img.shields.io/badge/tests-738%20passing-2ea44f)](#verified)
[![doctor](https://img.shields.io/badge/doctor-26%20checks-2ea44f)](#verified)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#requirements)
[![dependencies](https://img.shields.io/badge/dependencies-none-blue)](#requirements)
[![context](https://img.shields.io/badge/always--on%20context-332%20tokens-blue)](#context-cost)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**English** · [Русский](docs/README.ru.md) · [中文](docs/README.zh.md)

</div>

---

Already in a Claude Code session — the shortest path, no terminal:

```
/plugin marketplace add avanturer/claude-bestpractice
/plugin install claude-bestpractice
```

From a terminal, the same thing:

```sh
claude plugin marketplace add avanturer/claude-bestpractice
claude plugin install claude-bestpractice@claude-bestpractice
```

If that first line reaches for `git@github.com:` and stops on a missing SSH key, pass the
URL instead of the shorthand — reported from a real install, where the shorthand resolved
to SSH on a machine that had no key:

```sh
claude plugin marketplace add https://github.com/avanturer/claude-bestpractice
```

One thing that will not fix: a global `url.git@github.com:.insteadOf https://github.com/`
in your own git config rewrites this URL too. `git config --get-regexp '^url\.'` says
whether you have one.

Or, to have the gates proven on your machine **before** anything is registered:

```sh
curl -fsSL https://raw.githubusercontent.com/avanturer/claude-bestpractice/HEAD/install.sh | bash
```

The two are not equivalent, and the difference matters on your first day:

|  | `claude plugin install` | `install.sh` |
|---|---|---|
| Gates fire in a session | yes | yes |
| `claude-bp` in **your own terminal** | no | yes (symlinks into `~/.local/bin`) |
| Doctor run before anything is registered | no | yes |

Claude Code puts the plugin's `bin/` on the Bash tool's PATH automatically, so on the
marketplace path the commands below work **inside a session** and are `command not found`
in your shell.

The push gate does not wait for either of them. The first session started in a repository
that has no `pre-push` hook installs one, and says so on the board; after that it stays
quiet. `claude-bp-ci off` removes it and the removal sticks — the next session will not
put it back. It arms itself rather than waiting for a command because a gate that only
fires when someone remembers to run something is the gate this whole project exists to
replace — and verification found exactly that, an install reporting `✓ enabled` over a
push path with nothing on it.

In any repository afterwards — from your terminal, or by asking Claude to run them:

```sh
claude-bp init      # derive what it can from your code; ask you for what it cannot
claude-bp status    # everything at once
```

The installer runs the doctor **before** registering anything and refuses to install if
any gate fails to fire. Gates that silently do nothing are worse than no gates.

Read that claim narrowly, because it is narrow: the doctor builds its own throwaway
repository and attacks that. It proves **the gates work**, not that they are correctly
wired into *your* project — so "All 26 checks passed" is a statement about this software,
never a clean bill of health for your repository. `claude-bp status` is the one that
looks at yours.

---

## The problem

You build a product almost entirely through Claude. Three to eight sessions run at once,
in separate worktrees. You read almost none of the diffs. What goes wrong is measured,
not speculative:

| | |
|---|---|
| The agent says done, the code does not work | **0.97** submit rate against **0.65** test-verified resolve. Two different guard prompts moved it by **zero** |
| It edits correct code it was never asked to touch | **60–90 %** of runs across four frontier models, when abstaining was correct |
| Rules decay as the session grows | 0 % violation → **30 %** after one compaction → **78 %** after four |
| Too many rules collapse compliance | **93.8 %** perfect at 10 rules → **75 %** at 20 → **23.8 %** at 40 → **0 %** at 80 |
| Stale context is worse than none | Stale-only retrieval produced dead-API calls on **15/17** samples; no retrieval produced **0/17** |

Sources for every figure in [`docs/EVIDENCE.md`](docs/EVIDENCE.md).

## The design in one sentence

**Nothing that matters is asked of the model.** Every rule that must hold is enforced by
the harness or by git; the model's context carries only the handful of things no program
can check.

---

## What you get

### Memory that cannot rot

Three layers, split by *what makes each one false*.

| Layer | Contents | Why it stays true |
|---|---|---|
| **Derived** | Symbol map, test results, health numbers | Regenerated from code and stamped with its source commit. A stale artifact is a build failure, not a confident wrong answer |
| **Decided** | Product, non-goals, entities, glossary, decisions | Immutable. A decision is a historical fact — retired by a later record naming it, never by rewriting history |
| **Ephemeral** | Sessions, leases, claims, allowance | Per-session, gitignored, TTL'd, reaped |

Every claim carries the **git blob hashes** of the code it describes — never mtimes,
which a worktree checkout resets wholesale. When a subject is rewritten, the claim is
suppressed and counted, never silently deleted.

Every entity names a canonical identifier and its file. A rename **fails validation**
rather than leaving your memory describing something that no longer exists.

### Sessions that see each other

```
OTHER LIVE SESSIONS (2) — do not edit files they hold:
  - a3f81c22 on feat/export  [ledger-export]  active 40s ago
      touched: src/billing.js, src/csv.js
      holds: src/billing.js
      task: Add CSV export to src/billing.js

IN FLIGHT:
  - 0004 Fix rounding in invoice totals  [b7d29e01]
NEXT:
  - 0005 Add client search
(12 done)

health: 3 live session(s), 1 reaped, 4 open item(s), 1 stale (suppressed)
```

Editing a file another live session holds is **denied**, naming the owner. A crashed
session's leases and claims are released by the reaper — in whichever worktree holds
them — instead of staying in flight forever.

A session that goes quiet is **not** treated as dead. Reaping on silence meant a founder
who thought for fifteen minutes came back to a session whose every gate had stopped
enforcing, so death now requires the process to be gone or its pid recycled.

### A work ledger that merges

`.claude/claude-bestpractice/plan/{next,doing,done}/` — one file per task, state encoded in the
directory, so a transition is `git mv`. Five worktrees produce five clean adds instead of
five conflicting hunks in one JSON blob. Ids allocate against every sibling worktree,
because worktrees share the namespace before their files are ever committed.

### Completion is expensive to fake

The Stop gate **discards the agent's prose** and runs your test suite itself. What it
does *not* do is treat that as proof, and the honest version of the claim is worth
stating plainly, because six rounds of adversarial verification took the earlier version
apart four separate times:

> **The agent writes your code, your tests, your test command and your build files. Any
> check that reads the runner's output is reading something the agent can write.** This
> gate raises the cost of a false green from one line to several deliberate steps, and
> leaves a durable record of each. It does not make one impossible.

That is what got broken, in order: it trusted an artifact file (hand-written XML beat
it), then the exit code (`-` before a Makefile recipe), then the words "N failed" (stop
printing them), then the count "N passed" (`@echo '2 passed in 0.03s'`). Each fix moved
the forgery one level down rather than out.

So one signal is now taken from outside that loop: the gate **counts test declarations in
your source tree itself** and compares. A run that executes a fraction of what the tree
declares is recorded as unverified rather than green, and a red suite cannot be cleared by
a tree that has shed tests — deleting the failing test is the move a blocking gate most
invites, and it was the cheapest way out. Moving that number means writing real tests,
which is a price this plugin is content to charge.

Past prototype stage it additionally re-runs against a clean checkout of the committed
tree, which catches the green-here-red-there class — an uncommitted file, a local
environment variable.

It also escalates rather than wedging: after four blocked attempts it records an
unverified finish and lets the turn end, because a gate that blocks a founder's workflow
forever is a gate that gets uninstalled.

### A pull request is an obligation, not a notification

The failure this closes is a session that agrees the change, opens the pull request, and
then stops — waiting for an approval nobody asked it to wait for. The PR sits, the session
ends, and nothing in the repository remembers it.

There are exactly two ways it can end, and no third:

**Merged, by the session that opened it.** No approval step, because there is no reviewer.
A turn that tries to end on an open pull request is interrupted and told to merge it.

**Handed to you, with the blockers named.** When the final check finds something the merge
is *refused* — not negotiated, not repaired. That half is what makes the first half safe:
a model asked to make a branch mergeable will make it mergeable, and the moves available
at merge time are weakening an assertion, widening a tolerance, or reverting the change
that surfaced the problem. All three satisfy the letter. Which is acceptable is your call,
so the gate stops there and says so.

The check is the same one that runs before a PR is opened — unfinished merge, no commits,
red suite, never-verified branch, unverified finish, uncommitted work — plus the review
findings already on the board. All local: no network call, because this runs on every tool
call. `gh pr merge` is watched alongside the structured tool, since a gate that only sees
one of them is one an agent walks past on its first `Bash` call.

It interrupts **once** per pull request and then carries it on the board, so ignoring it,
crashing, or hitting the escalation ceiling cannot turn a reminder into a wedge. Off with
`{"manage_pull_requests": false}`.

### Slop caught mechanically — in your repository

On the lines this turn added, the commit reviewer flags seven classes: swallowed
exceptions, bare excepts, debug leftovers, disabled verification, skipped tests, shell
injection and SQL interpolation — plus anything shaped like a credential. Pre-existing
matches are ignored, so it never bills you for someone else's history.

It **reports**, it does not block. A reviewer that refuses a commit over style is a
reviewer you switch off in a week, and a switched-off reviewer catches nothing.

### And the stricter rules, which are this project's own

`Args:` / `Returns:` / `:param:` banned outright because types already say it; a signature
change with an unchanged docstring failing the build; single-caller abstractions, compat
shims with no consumers, duplicate blocks and unused parameters at a permanent budget of
zero; complexity and length ratcheted downward only.

**Those run against this repository, not against yours.** They live in `tools/` and are
enforced by this project's own `make check`. They are here to be read, checked against the
source you are about to install, and copied if you want them — not because your code is
exempt, but because a plugin that imposed its author's style on your build is a plugin
that gets uninstalled on day one.

> If you are about to write a comment describing what a value is, write a type instead.

### Rigor that scales itself

Stage is computed from the repository, never configured, and the ratchet only tightens.

| Signal detected | What switches on |
|---|---|
| CI plus a deploy target | Egress rules, production-signal airlock |
| A migration creating a users table, or an auth SDK | Migration gating, production-promotion denial, per-worktree database and port |


### Checks run locally, not on someone's meter

Your first session installs a **pre-push hook** that runs your own `make check`, or your
project's own test command, before anything leaves the machine. Free, and in time to stop
the push rather than to email you about it afterwards.

If that command's runner is missing when you push, the hook **refuses** rather than
falling through to a cheerful exit code: this project has a suite, so a push reported as
checked while nothing ran is the one outcome worse than a red one. A repository with no
suite at all is a different case and is allowed through, because nothing is being skipped.

That is the default because hosted minutes are metered and this operating mode spends
them like a small team: three to eight sessions pushing all day, billed to one account.

The shipped GitHub Actions workflow is **gated behind a repository variable**, so it
costs nothing until you ask for it:

```sh
claude-bp-ci status     # what runs where
claude-bp-ci github     # switch hosted CI on (sets the variable via gh)
claude-bp-ci off        # remove the pre-push hook
```

Both can run at once — a pre-push hook only binds machines that installed it, so a
repository other people push to still wants the hosted run. Bypass once with
`git push --no-verify`; that is deliberate and leaves a record, which a silently skipped
hosted run does not.

### It takes over what fights it

`claude-bp adopt` finds other tools contesting the events this owns, quarantines their
hook entries into a labelled block with a backup, and tells you exactly which competing
plugins to disable. Reversible with `--restore`. It never deletes another tool's
configuration silently.

---

## Commands

| | |
|---|---|
| `claude-bp status` | Sessions, plan, knowledge, memory health, conflicts, next action |
| `claude-bp init` | Derive the knowledge layer from your code |
| `claude-bp adopt` | Take over events another tool is contesting |
| `claude-bp doctor` | Prove each gate by attempting a known-bad action |
| `claude-bp-plan` | The work ledger: `add`, `list`, `claim`, `done` |
| `claude-bp-decide` | Accept a decision drafted from your own corrections |
| `claude-bp-ingest` | Sanitise production errors into fenced task files |
| `claude-bp-knowledge` | Validate the decided layer, refresh its index |
| `claude-bp-reindex` | Drop and rebuild everything derived |
| `claude-bp-ci` | Where the checks run: local pre-push by default, hosted CI opt-in |
| `claude-bp-attempt` | The dead-end ledger: what was tried, and why it failed |
| `claude-bp-options` | Record a decision as a scored comparison of alternatives |
| `claude-bp-ship` | What this branch delivered, for someone who never reads code (`--pr` opens one) |

In a session: `/claude-bestpractice:status` · `/claude-bestpractice:plan` · `/claude-bestpractice:review`

## The gates

| Gate | Event | Posture | Does |
|---|---|---|---|
| `setup` | Setup | fails open | Derives the knowledge layer, creates the plan, seeds the stage |
| `session-start` | SessionStart | fails open | Reaps the dead, registers, injects board + plan + stage |
| `prompt-capture` | UserPromptSubmit | fails open | Records the verbatim task. **Injects nothing** |
| `pre-tool` | PreToolUse | **fails closed** | Call ceiling, loop break, secret pre-write scan, leases, migration and deploy gating |
| `review-commit` | `if: Bash(git commit:*)` | async rewake | Reviews this turn's diff; wakes you only when there is something to say |
| `worktree-create` | WorktreeCreate | fails open | Names it, seeds trust, derives a private port and database |
| `subagent-brief` | SubagentStart | fails open | Non-goals, entities and a query-biased map to agents that inherit no rules |
| `checkpoint` | PreCompact | fails open | Extractive checkpoint, zero model calls, secrets scrubbed |
| `evidence-gate` | Stop | **fails closed** | Scope drift, test evidence, clean re-run; harvests decision drafts |

Nine entries against a self-imposed budget of twelve. Always-on context **~332 tokens**
against a cap of 400 — roughly 0.1 % of a 200k window.

---

## Verified

```
make check    # lint · docs gate · slop gate · polyglot gate · knowledge · 738 tests · 26 doctor checks · budget
```

The doctor proves gates by **attempting the bad thing**, not by reading configuration
back — config-readback cannot detect a semantics change. Seventeen real bugs during
development were invisible to inspection and caught only by execution, including a
deadlock where the evidence gate demanded a test artifact and then blocked the session
for producing it.

The suite includes a full project lifecycle driven entirely through the real gate
executables: onboarding an unseen repository, planning, a leaked credential, a scope
violation, a failing suite, a green finish, and a second session reading the history.

## Upgrading

```sh
claude plugin marketplace update claude-bestpractice
claude plugin update claude-bestpractice@claude-bestpractice
```

Then **restart Claude Code**. The update lands in a new directory and every session
already running keeps executing the old copy until it is restarted — the CLI says
`Restart to apply changes.` once and never mentions it again. A session that is running
superseded code now says so on its own board, which is the only place you would find out.

The second command needs the **qualified** `name@marketplace` form. `install` accepts the
short name and `update` does not — the short form fails with `Plugin "claude-bestpractice"
not found` while the plugin is installed and enabled, which reads as a broken install
rather than a wrong argument. Verified by upgrading 1.0.1 to 1.0.2 on a real install.

**The version string is the update key, and this is worth knowing because it can strand
you silently.** `claude plugin update` compares the installed version against the
marketplace's and stops there. Measured, not inferred: a local marketplace, an install, a
changed file with the version left alone, then

```
$ claude plugin update claude-bestpractice@claude-bestpractice
claude-bestpractice is already at the latest version (1.0.0).
```

The changed file never reached the cache. There is no observable difference between "up to
date" and "permanently stranded" — both print a tick. So a change under `plugin/` that does
not bump the version now **fails this project's own build**, in `tools/check_shipped.py`.
The gate exists because the alternative is a fix that cannot reach the person who needs it.

Your state is untouched by an upgrade, also verified rather than assumed: a plan task
written under 1.0.1 was still there and still readable after. It lives in your repository
and in your git common directory, never in the plugin cache, which is what the version
bump replaces.

Installed with `install.sh` instead? That path is a clone, so it updates with `git pull`
in the directory you cloned into, and needs no version bump to do it.

## Requirements

Python 3.9+ and git. **No other dependency, by constraint** — these hooks run on every
tool call, so a dependency tree is latency, an extra failure mode and a supply-chain
surface for the component whose whole job is to be trustworthy. Enforced in CI.

Tested on Python 3.9, 3.11 and 3.13. `claude plugin validate --strict` passes against
Claude Code 2.1.220.

### What appears in your repository, and what to do with it

Two directories, and the difference matters:

**`.claude/claude-bestpractice/` — commit this.** Tasks, decisions, dead ends, the stage marker.
It is deliberately inside your repository because it must travel with the branch: a
decision taken on `feat/billing` is about `feat/billing`, and a task list that does not
follow a branch switch is worse than none. One file per item, so five worktrees produce
five clean adds rather than five conflicting hunks in one blob. Nothing in it varies
between runs, so it does not turn up as a diff every time a gate fires.

**`.git/claude-bestpractice/` — ignore it, git already does.** Live sessions, leases, the
loop counter, the test receipt. It lives in the git common directory because that is the
only path shared by every worktree of one clone, invisible to git, surviving branch
switches, and dying with the clone. It is entirely rebuildable: `claude-bp-reindex`
regenerates it from the committed half, and that path is tested rather than assumed.

You never edit either by hand. If you want the whole thing gone, delete both directories
and uninstall the plugin — nothing else on your machine is touched.

### Context cost

Claude Code's `/plugin` panel shows what a plugin adds to your context window every
turn, which makes this a number you can compare rather than a claim you have to take.

claude-bestpractice holds **~332 tokens** of always-on context across four components, against a
self-imposed ceiling of 400. `make check` fails the build if it goes over, and the
ceiling has been held by trimming descriptions rather than by raising it — twice.

Everything else the plugin knows is loaded on demand: skills only when their trigger
fires, the board only at session start, the work ledger only when asked for. The budget
exists because the cost is itemised in your own usage view while the benefit is
counterfactual and invisible, which is a trade a founder should get to audit.

---

## What this deliberately is not

- **Not a memory engine.** The harness stores and loads memory. This owns curation.
- **Not a code reviewer.** Several first-party review paths exist; pick one and integrate.
- **Not a task manager.** The native task system is subsumed and gated, never replaced.
- **Not for teams.** Every trade-off assumes one owner and no reviewer.

## Four things it cannot enforce

Stated up front, because the default in this category is false assurance.

1. **Test semantics.** No matcher distinguishes a justified skip from a cheat.
2. **Taste.** No matcher distinguishes good design from bad.
3. **`claude --bare`.** It drops managed and plugin hooks alike. That is why the repo
   layer — real git hooks, CI, branch protection — exists.
4. **A human with root.** By design.

---

## Documentation

| | |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | Thesis, architecture, memory model, substrate, verification |
| [`docs/ENFORCEMENT.md`](docs/ENFORCEMENT.md) | Binding vs advisory, the ten-rule budget, what cannot hold |
| [`docs/ECONOMICS.md`](docs/ECONOMICS.md) | Token budget, prompt-cache invariants, rate-limit admission control |
| [`docs/EVIDENCE.md`](docs/EVIDENCE.md) | Every measured claim with its source and evidence tier |
| [`docs/LIMITS.md`](docs/LIMITS.md) | **What a false green still costs** — eight rounds of attacks, and the ones that still work |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What shipped, and the bugs that only execution found |

MIT.
