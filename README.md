# founder-os

A control plane for building products with several Claude Code sessions running at once
on one repository. Install it once; it works in every project after that.

```sh
curl -fsSL https://raw.githubusercontent.com/avanturer/claude-bestpractice/main/install.sh | bash
```

Then, in any repository:

```sh
founder-os init      # derive the knowledge layer from the code that is already there
founder-os status    # what is known, in flight, planned and enforced
founder-os doctor    # prove every gate still fires
```

Nothing else to configure. The installer refuses to register the plugin if the doctor
fails, because gates that silently do nothing are worse than no gates.

---

## What it does

**Memory that cannot go stale.** Three layers split by what makes each one false.

| Layer | Contents | Why it cannot rot |
|---|---|---|
| **Derived** | Symbol map, route manifest, test results, health numbers | Regenerated from code, stamped with the commit it came from. A stale artifact is a build failure, not a confident wrong answer |
| **Decided** | Product, non-goals, entities, glossary, decision records | Immutable. A decision is a historical fact; it is retired by a later record naming it, never by rewriting history |
| **Ephemeral** | Session registry, leases, plan claims, allowance | Per-session, gitignored, TTL'd, reaped |

Every persisted claim carries the **git blob hashes** of the code it describes — never
mtimes, which a worktree checkout resets wholesale. A claim whose subject was rewritten
is suppressed from injection and counted, never silently deleted.

Every entity names a canonical identifier and its file. A rename **fails validation**
rather than leaving the layer describing something that no longer exists.

**Parallel sessions that see each other.** A session board injected at start: who else
is running, on what branch and worktree, what they last touched, which files they hold.
Editing a file another live session holds is **denied**, with the owner's id. A crashed
session's claims are released by the reaper rather than held forever.

**A work ledger.** `.claude/founder-os/plan/{next,doing,done}/` — one file per task,
state encoded in the directory, so a transition is `git mv` and five worktrees produce
five clean adds instead of five conflicting hunks in one JSON blob.

**Completion accepted on evidence, never on assertion.** The Stop gate discards the
agent's prose and requires a machine-readable test artifact that exists, is newer than
the newest changed file, and passes when re-run from a clean checkout.

**Code written for the next model to read.** Docstrings must carry non-derivable
information; `Args:`/`Returns:`/`:param:` are banned because types already say it. A
signature change with an unchanged docstring fails the commit.

**Slop caught mechanically.** Swallowed exceptions, single-caller abstractions,
compat shims with no consumers, duplicate blocks, unused parameters — permanent budget
of zero. Complexity and length are ratcheted: baselined once, then only downward.

**Rigor that scales itself.** Stage is computed from the repository, never configured,
and the ratchet only tightens.

| Signal | What turns on |
|---|---|
| CI plus a deploy target | Egress rules, production-signal airlock |
| A migration creating a users table, or an auth SDK | Migration gating, production-promotion denial, per-worktree database and port |
| A payment SDK, or a live-mode key shape | Triple-run verification for anything touching auth, money or schema |

A prototype gets none of it, and additionally has back-compat shims **banned** — that
rule disables itself once real consumers appear.

**Zero nagging.** A failing check is a prompt for the agent, not an interruption for
you. Denials talk to the model, which self-corrects silently.

---

## Commands

| | |
|---|---|
| `founder-os status` | Everything at once: sessions, plan, knowledge, memory health, next action |
| `founder-os init` | Derive the knowledge layer from the code |
| `founder-os-plan` | The work ledger: `add`, `list`, `claim`, `done` |
| `founder-os-decide` | Accept a decision drafted from your own corrections |
| `founder-os-doctor` | Prove each gate by attempting a known-bad action |
| `founder-os-ingest` | Sanitise production errors into fenced task files |
| `founder-os-knowledge` | Validate the decided layer, refresh its index |
| `founder-os-reindex` | Drop and rebuild all derived state |

Inside a session: `/founder-os:status`, `/founder-os:plan`, `/founder-os:review`.

---

## The gates

| Gate | Event | Posture | Does |
|---|---|---|---|
| `setup` | Setup | fails open | Derives the knowledge layer, creates the plan, seeds the stage |
| `session-start` | SessionStart | fails open | Reaps the dead, registers, injects the board, plan and stage |
| `prompt-capture` | UserPromptSubmit | fails open | Records the verbatim task. **Injects nothing** |
| `pre-tool` | PreToolUse | **fails closed** | Call ceiling, loop break, secret pre-write scan, file leases, migration and deploy gating |
| `review-commit` | `if: Bash(git commit:*)` | async rewake | Reviews this turn's diff against a moving baseline; wakes you only when there is something to say |
| `worktree-create` | WorktreeCreate | fails open | Names it, seeds trust, derives a private port and database |
| `subagent-brief` | SubagentStart | fails open | Non-goals, entities and a query-biased map to agents that inherit no rules |
| `checkpoint` | PreCompact | fails open | Extractive checkpoint, zero model calls, secrets scrubbed |
| `evidence-gate` | Stop | **fails closed** | Scope drift, test evidence, clean re-run; harvests decision drafts |

Nine hook entries against a budget of twelve. Always-on context: **~195 tokens** against
a cap of 400 — about 0.1 % of a 200k window.

---

## Verified

```
make check    # lint · docs gate · slop gate · knowledge · 345 tests · 20 doctor checks · budget
```

Python 3.9+, **standard library only** — enforced, because these hooks run on every tool
call and a dependency is latency, a failure mode and a supply-chain surface. CI on 3.9,
3.11 and 3.13. `claude plugin validate --strict` passes against CLI 2.1.220.

The doctor proves gates by **attempting the bad thing**, not by reading config back —
config-readback cannot detect a semantics change, and eleven real bugs during
development were invisible to inspection and caught only by execution.

---

## What this deliberately does not do

- **Not a memory engine.** The harness stores and loads memory. This owns curation.
- **Not a code reviewer.** Several first-party review paths exist; pick one, integrate.
- **Not a task manager.** The native task system is subsumed and gated, never replaced.
- **Not for teams.** Every trade-off assumes one owner and no reviewer.

## Four things it cannot enforce, stated up front

1. **Test semantics.** No matcher distinguishes a justified skip from a cheat.
2. **Taste.** No matcher distinguishes good design from bad.
3. **`claude --bare`.** It drops managed and plugin hooks alike. That is why the repo
   layer — real git hooks, CI, branch protection — exists.
4. **A human with root.** By design.

Design rationale in [`docs/DESIGN.md`](docs/DESIGN.md), the enforcement ladder in
[`docs/ENFORCEMENT.md`](docs/ENFORCEMENT.md), the token model in
[`docs/ECONOMICS.md`](docs/ECONOMICS.md), and every measured claim with its source in
[`docs/EVIDENCE.md`](docs/EVIDENCE.md).
