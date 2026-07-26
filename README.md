<div align="center">

# founder-os

**Memory, coordination and enforcement for building products with several Claude Code sessions at once.**

[![version](https://img.shields.io/badge/version-1.0.0-black)](https://github.com/avanturer/claude-bestpractice/releases)
[![tests](https://img.shields.io/badge/tests-525%20passing-2ea44f)](#verified)
[![doctor](https://img.shields.io/badge/doctor-25%20checks-2ea44f)](#verified)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#requirements)
[![dependencies](https://img.shields.io/badge/dependencies-none-blue)](#requirements)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**English** · [Русский](docs/README.ru.md) · [中文](docs/README.zh.md)

</div>

---

```sh
claude plugin marketplace add avanturer/claude-bestpractice
claude plugin install founder-os@founder-os
```

Or, to have the gates proven on your machine **before** anything is registered:

```sh
curl -fsSL https://raw.githubusercontent.com/avanturer/claude-bestpractice/HEAD/install.sh | bash
```

That is the whole setup. In any repository afterwards:

```sh
founder-os init      # derive what it can from your code; ask you for what it cannot
founder-os status    # everything at once
```

The installer runs the doctor **before** registering anything and refuses to install if
any gate fails to fire. Gates that silently do nothing are worse than no gates.

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

`.claude/founder-os/plan/{next,doing,done}/` — one file per task, state encoded in the
directory, so a transition is `git mv`. Five worktrees produce five clean adds instead of
five conflicting hunks in one JSON blob. Ids allocate against every sibling worktree,
because worktrees share the namespace before their files are ever committed.

### Completion accepted on evidence

The Stop gate **discards the agent's prose** and runs your test suite itself, treating
its own observed exit code as the evidence. A file claiming the tests passed is an
assertion with angle brackets: a hand-written one, one from another project, and `touch
junit.xml` all defeated an earlier artifact-reading version of this gate.

Past prototype stage it additionally re-runs against a clean checkout of the committed
tree, which catches the green-here-red-there class — an uncommitted file, a local
environment variable.

It also escalates rather than wedging: after four blocked attempts it records an
unverified finish and lets the turn end, because a gate that blocks a founder's workflow
forever is a gate that gets uninstalled.

### Code written for the next model to read

Docstrings must carry non-derivable information. `Args:` / `Returns:` / `:param:` are
banned outright — types already say it. A signature change with an unchanged docstring
**fails the commit**.

> If you are about to write a comment describing what a value is, write a type instead.

### Slop caught mechanically

Swallowed exceptions, single-caller abstractions, compat shims with no consumers,
duplicate blocks, unused parameters — **permanent budget of zero**. Complexity and
length are ratcheted: baselined once, then only downward.

### Rigor that scales itself

Stage is computed from the repository, never configured, and the ratchet only tightens.

| Signal detected | What switches on |
|---|---|
| CI plus a deploy target | Egress rules, production-signal airlock |
| A migration creating a users table, or an auth SDK | Migration gating, production-promotion denial, per-worktree database and port |


### Checks run locally, not on someone's meter

`founder-os init` installs a **pre-push hook** that runs your own `make check` — or the
doctor, in a repository that has none — before anything leaves the machine. Free, and in
time to stop the push rather than to email you about it afterwards.

That is the default because hosted minutes are metered and this operating mode spends
them like a small team: three to eight sessions pushing all day, billed to one account.

The shipped GitHub Actions workflow is **gated behind a repository variable**, so it
costs nothing until you ask for it:

```sh
founder-os-ci status     # what runs where
founder-os-ci github     # switch hosted CI on (sets the variable via gh)
founder-os-ci off        # remove the pre-push hook
```

Both can run at once — a pre-push hook only binds machines that installed it, so a
repository other people push to still wants the hosted run. Bypass once with
`git push --no-verify`; that is deliberate and leaves a record, which a silently skipped
hosted run does not.

### It takes over what fights it

`founder-os adopt` finds other tools contesting the events this owns, quarantines their
hook entries into a labelled block with a backup, and tells you exactly which competing
plugins to disable. Reversible with `--restore`. It never deletes another tool's
configuration silently.

---

## Commands

| | |
|---|---|
| `founder-os status` | Sessions, plan, knowledge, memory health, conflicts, next action |
| `founder-os init` | Derive the knowledge layer from your code |
| `founder-os adopt` | Take over events another tool is contesting |
| `founder-os doctor` | Prove each gate by attempting a known-bad action |
| `founder-os-plan` | The work ledger: `add`, `list`, `claim`, `done` |
| `founder-os-decide` | Accept a decision drafted from your own corrections |
| `founder-os-ingest` | Sanitise production errors into fenced task files |
| `founder-os-knowledge` | Validate the decided layer, refresh its index |
| `founder-os-reindex` | Drop and rebuild everything derived |
| `founder-os-ci` | Where the checks run: local pre-push by default, hosted CI opt-in |
| `founder-os-attempt` | The dead-end ledger: what was tried, and why it failed |
| `founder-os-options` | Record a decision as a scored comparison of alternatives |
| `founder-os-ship` | What this branch delivered, for someone who never reads code (`--pr` opens one) |

In a session: `/founder-os:status` · `/founder-os:plan` · `/founder-os:review`

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

Nine entries against a self-imposed budget of twelve. Always-on context **~329 tokens**
against a cap of 400 — roughly 0.1 % of a 200k window.

---

## Verified

```
make check    # lint · docs gate · slop gate · polyglot gate · knowledge · 540 tests · 25 doctor checks · budget
```

The doctor proves gates by **attempting the bad thing**, not by reading configuration
back — config-readback cannot detect a semantics change. Seventeen real bugs during
development were invisible to inspection and caught only by execution, including a
deadlock where the evidence gate demanded a test artifact and then blocked the session
for producing it.

The suite includes a full project lifecycle driven entirely through the real gate
executables: onboarding an unseen repository, planning, a leaked credential, a scope
violation, a failing suite, a green finish, and a second session reading the history.

## Requirements

Python 3.9+ and git. **No other dependency, by constraint** — these hooks run on every
tool call, so a dependency tree is latency, an extra failure mode and a supply-chain
surface for the component whose whole job is to be trustworthy. Enforced in CI.

Tested on Python 3.9, 3.11 and 3.13. `claude plugin validate --strict` passes against
Claude Code 2.1.220.

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
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What shipped, and the bugs that only execution found |

MIT.
