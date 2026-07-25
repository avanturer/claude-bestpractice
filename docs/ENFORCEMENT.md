# Enforcement

> **Scope and intent.** This document describes how the *owner of a machine* hardens their *own*
> agent sessions using documented Claude Code settings. The threat model is **an agent doing damage
> by accident or by confabulation**, not a human being prevented from administering their own
> computer. A human with `sudo` can always disable all of this, and should be able to.
>
> Everything below uses documented configuration only. Behaviour inferred from disassembly was
> deliberately excluded from the design: it has no stability contract and would break silently.
> Anything marked UNVERIFIED must be proven against the installed binary before it is relied upon.

## 1. The ladder

Anthropic classifies its own instruction files as advisory, in writing:

> *"CLAUDE.md content is delivered as a user message after the system prompt, not as part of the
> system prompt itself. Claude reads it and tries to follow it, but there's no guarantee of strict
> compliance."*
>
> *"Claude treats them as context, not enforced configuration. To block an action regardless of what
> Claude decides, use a PreToolUse hook instead."*

| Tier | Mechanism | Property |
|---|---|---|
| **BINDING** | `permissions.deny` | Outranks everything, including hooks and bypass mode. `deny` → `ask` → `allow`, first match wins, specificity irrelevant |
| **BINDING** | Hooks (`command` type, exit 2) | Guaranteed execution by the harness. `PreToolUse` exit 2 blocks *before* permission rules are evaluated |
| **BINDING** | OS sandbox (Seatbelt / bubblewrap) | Kernel-level, applies to Bash and all child processes |
| **HYBRID** | Hook-injected context (`additionalContext`) | **Delivery** is binding and deterministic; **compliance** stays advisory |
| **ADVISORY** | `CLAUDE.md`, `.claude/rules/*.md`, output styles, skill bodies | The model may ignore it |

### The hybrid tier is where most rules belong

Hook-injected context fixes `CLAUDE.md`'s three real failure modes — dilution in a long file, loss
after compaction, and non-inheritance by subagents — without pretending to be enforcement. Fifteen
lines of live rules re-injected on a compaction boundary beats a 400-line file read once at startup.

## 2. The rule budget: ten

The evidence is a cliff, not a slope, and it is measured on Claude specifically. Perfect-response
rate for Claude Sonnet 5 across 4,800 programmatically-scored trials:

| Simultaneous rules | 10 | 20 | 40 | 80 |
|---|---|---|---|---|
| Perfect response | **93.8 %** | 75.0 % | 23.8 % | **0.0 %** |

Every model, every format, both placements hit exactly zero by 80 and stay there to 160. An
independent measurement finds the same knee: joint compliance 99.0 → 92.5 → 80.6 → 47.5 % across one
to five stacked constraints, and it compounds under cognitive load — 86.8 % at zero extra load versus
69.1 % with three concurrent problems. Our agent is always at maximum load, so assume the loaded
curve.

Rules are not free even when redundant: adding five *self-evident* constraints — ones back-translated
from the model's own already-correct answer — cost Claude Sonnet 4.5 **15 %** of its multi-hop QA
capability.

Nobody notices crossing 40, because per-rule compliance still looks fine while the all-rules metric
has already collapsed. **Count the rules in CI.**

**Ceiling: 10 always-on prompt rules. Hard maximum 15.**

### What the ten may be

Reserve the budget for rules that (a) no program can check, (b) must fire before the first tool call,
and (c) can be phrased as commissions. Candidates: which worktree and branch this session owns; where
task state lives and that it must be read first; the two or three architectural conventions no linter
encodes; the escalation rule for ambiguity.

### Phrasing is load-bearing

Omission-type constraint compliance decays **73 % → 33 %** between turns 5 and 16, while
commission-type compliance holds at **100 % in the same responses** (p < 1e-33 across twelve models).
The driver is token volume, not turn count. Mechanistically, 87.5 % of prohibition violations are
priming failures: naming the forbidden thing inside the prohibition activates it more than the
negation suppresses it.

Write *"Every file edit happens inside the worktree this session owns"* — never *"Do not edit files
outside your worktree."*

### What must move out of the budget

1. **Every prohibition.** They are the decaying class, they cost budget, and they prime the
   behaviour. Move each to a deny rule and delete the sentence.
2. **Anything with a deterministic check** — branch discipline, file ownership, formatters, secret
   patterns, protected paths.
3. **Every claim of success** → an execution-verified gate.
4. **Task-state transitions** → a task-lifecycle hook.

### What must not be built

**A per-turn natural-language reminder.** It fails on the evidence, and it is quadratically
expensive — see [`ECONOMICS.md`](ECONOMICS.md).

## 3. Compaction is the largest destroyer of in-context rules

| Compactions | 0 | 1 | 4 |
|---|---|---|---|
| Violation rate | **0 %** | 30 % pooled, 59 % worst case | **78 %** |

It is 8.3× worse for exactly our class of rule: soft, organisation-specific policy decayed +50 pp
versus +6 pp for hard safety norms — RLHF priors mask the effect on *never exfiltrate secrets* and do
nothing for *always work in a worktree*.

**Mitigation: constraint pinning.** Re-inject the rule block **verbatim, not re-summarised**, on
SessionStart with matcher `startup|resume|clear|compact`, plus on the post-compaction event. Measured
to restore 0 % violation across seven models at under 0.5 % token overhead with no utility cost.

Do not rely on the pre-compaction hook as a veto: blocking it leaves the session running uncompacted
while context keeps growing. Use it to **flush**, never to refuse. And design so that losing any
prior tool output is a non-event — **persist at write time, never at compaction time**.

## 4. Subagent inheritance

Non-fork subagents inherit the full `CLAUDE.md` hierarchy — **but the built-in Explore and Plan
agents skip it, and there is no setting to change that.** Output styles and auto memory never reach
any non-fork subagent.

With 3–8 sessions each spawning Explore and Plan, a large fraction of agent-turns run with **zero
project rules loaded**.

Do not fight it. Hooks and permission rules are process-global and *do* apply inside subagents —
verified: a spawned subagent had `PreToolUse` fire with `agent_id`/`agent_type` in the payload, was
blocked by a hook deny, and was blocked by path and command deny rules, all in one run.
**Enforcement inherits; awareness does not.** Keep Explore and Plan for their context efficiency,
accept they are rule-blind, and hand them a deliberately minimal block via the subagent-start hook.

## 5. Holes that must be closed on day one

Each is stated as *what leaks* and *what closes it*. Ordered by priority.

| # | What leaks | Closure |
|---|---|---|
| 1 | A nested agent process started with its own configuration ignores the outer policy | Managed-scope hook policy plus deny rules on invoking the CLI and on config-override flags, plus a pre-tool gate that resolves `argv[0]` |
| 2 | **Any interpreter defeats every file permission rule.** A three-line Python script writes to a denied path; the documentation says so plainly — deny rules *"don't apply to arbitrary subprocesses that read or write files indirectly"* | **OS sandbox only.** There is no string-matching closure; the space of interpreters and encodings is unbounded |
| 3 | **Hooks failing open.** Exit code 1 is a *non-blocking* error and the tool runs. Only exit 2 blocks — this inverts Unix convention and is the highest-probability real defect in the design, and it is silent | `set -euo pipefail` with `trap 'exit 2' ERR`; parse stdin and exit 2 on unparseable input; a doctor that *proves* each gate fires |
| 4 | Self-tamper: the agent edits settings or rewrites a guard script to exit 0 | Sandbox auto-denies settings writes at every scope; deny rules on the paths; guard scripts live root-owned outside the plugin root (which is user-writable and changes on every update); a SHA-256 manifest verified at SessionStart against a copy stored outside agent-writable space |
| 5 | An untrusted worktree silently drops all project allow rules, hooks and status line | Pre-seed trust from the worktree-create hook; assert at SessionStart |
| 6 | Environment-runner allow rules act as a blank cheque — the wrapper-stripping list is fixed and excludes common task runners | Never write a runner-prefix allow rule. One exact-match rule per inner command |
| 7 | **MCP tools write files outside every Bash matcher and outside the sandbox** — a remote server writing through an API bypasses all local enforcement. The widest un-sandboxable hole in the design | Managed MCP allowlist (mandatory, not optional), pre-tool matchers on the MCP namespace, and disabling side-load flags |
| 8 | Sandbox present but silently unenforcing when its backend is missing | `failIfUnavailable: true`. Without it every guarantee in the product is fiction |

### Two closures that turn out to be unnecessary

Command chaining is already handled: `a && b`, `a; b`, `$(...)` and leading environment assignments
are all correctly matched by deny rules. The remaining gap is `eval` on a runtime-constructed string
— deny `eval` outright. Do not waste hook logic re-implementing what the matcher already does.

## 6. Silent correction beats refusal — with one hard condition

A pre-tool hook can rewrite a tool call rather than reject it: strip a verification-skipping flag,
inject a frozen-lockfile flag, normalise a commit message. Zero friction, zero nagging, and nobody in
the surveyed ecosystem uses it.

**The condition:** a rewrite must be visible in the tool result the model sees. A silent rewrite
means the model believes it did X, the transcript says it did X, the compaction summary will record X
as a confirmed result — and it did not happen. That is not a coordination feature, it is a
manufactured false belief propagated into every future session. Observed in practice: the model
noticed a rewrite and commented on it unprompted. Binding, but not invisible — and it must stay that
way.

## 7. What we own, disable, or coexist with

| Feature | Verdict | Why |
|---|---|---|
| Auto memory | **Disable** | Keyed on the git repo, so all worktrees write into one truncation-limited file; re-injected after compaction so it outlives our context; never loaded into subagents. Two unreconciled memories is the exact failure this product exists to prevent |
| Bundled skills and keyword-triggered workflows | **Disable, then re-provide** | Hidden from the model, still typable by the human — the founder keeps every escape hatch while the agent stops choosing someone else's opinions over ours. All-or-nothing, so we inherit responsibility for what we hid |
| File checkpointing / rewind | **Disable, replace with git-backed undo** | Snapshots only files touched by edit tools — never bash-mediated changes, never subagent edits. Under parallel worktrees a rewind in session A can silently revert what session B wrote |
| Built-in task system | **Subsume, do not replace** | Mirror into our store and veto illegitimate transitions with task-lifecycle hooks. Our rules become unbypassable precisely by sitting *on* the native store rather than beside it, and the founder keeps the UI they already read |
| Native worktree tooling | **Own via configuration** | Never reimplement. Configure it and hook the lifecycle; treat the harness as the source of truth |
| Ancestor and third-party `CLAUDE.md` | **Exclude** | All discovered files are *concatenated*, not overridden, and *"if two rules contradict each other, Claude may pick one arbitrarily."* One past install of an unrelated tool can silently contradict us in every project on the machine |
| Other plugins' skills, agents, hooks, MCP | **Disable at the harness level** | The documented, purpose-built lock. Note that skill-override settings do **not** affect plugin skills, and name collisions fail silently, first-loaded-wins |
| Built-in git instructions and attribution | **Own** | Removes a system-prompt-level competitor to our worktree discipline. It does not block git, so pair it with the pre-tool gate |
| The nag surface | **Disable globally** | Notifications, spinner tips, surveys, push and input-needed alerts. **Keep the away-summary and recap** — they are the built-in zero-nag re-entry affordance and shipping a competing digest would be redundant |
| Auto-mode classifier | **Disable, or own entirely** | A second, model-driven permission authority whose decisions we did not make; its block thresholds are not configurable and cause prompting exactly when unattended sessions matter most. If kept, omitting the defaults token *replaces* the entire built-in list and silently discards force-push and pipe-to-shell protections |
| Inline shell execution inside skills | **Disable** | Any user- or project-authored skill can execute shell *at load time* with no tool call, sidestepping every pre-tool guard. Bundled and managed skills are unaffected, so our own keep the capability |
| HTTP-type hooks | **Lock, and never use for our own gates** | Non-2xx, connection failures and timeouts are non-blocking — they **fail open**. Use command-type for everything that must hold |
| Auto-compaction | **Coexist** | Disabling it just trades a lossy summary for hard context overflow |
| Micro-compaction | **Coexist defensively** | No documented off switch. Design so that losing prior tool output is a non-event |
| Official security plugin | **Delegate entirely** | Better than anything a v1 would write; duplicating it produces two independent interruptions for one action |
| Loose hooks written by other installers | **Own the file carefully** | Not plugins, so invisible to plugin listings, and they will silently double-fire. Back up, quarantine into a labelled block, report once, reconcile on every run. **Never silently delete a user's hooks** |

## 8. What cannot be enforced

Stated plainly, because the category default is false assurance — three shipped, starred "security"
plugins in the largest third-party marketplace enforce literally nothing, one of them passing
environment variables the documentation says do not exist, another using a singular key where the
schema requires an array.

1. **Test semantics.** No matcher distinguishes a justified skip from a cheat. Detection only.
2. **Architecture, naming and taste.** Post-hoc diff audit only, routed through a read-only reviewer
   and treated as advice, never as a gate.
3. **Bare mode.** It drops managed hooks and plugin hooks alike, and nothing in-product covers it.
   The residual defence is the repo layer — real git hooks under a root-owned hooks path, CI, and
   branch protection with required status checks the agent has no credentials to alter.
4. **A human with `sudo`.** By design.

## 9. Fragility and version detection

There is no stability contract: 353 released versions, a release essentially every day, no semver
guarantee, no deprecation policy. Two documented behaviours have already changed under us — a hook
condition silently narrowed its path matching, and the worktree base-ref default flipped back.

- Hook payloads carry **no version field**. Shell out to `claude --version` at SessionStart and cache
  the parsed semver.
- Pin the update channel to stable (roughly a week behind, skipping versions with major regressions)
  and declare a required minimum version — the floor where our semantics are guaranteed.
- Structure guards as **capability-gated tiers**: every feature we use has a named fallback, and the
  doctor reports which tier is active.
- **Verify by proving, not by reading.** The doctor attempts a known-bad action in a scratch
  directory and asserts the denial. Config-readback cannot detect a semantics change. Run it on every
  CLI upgrade, not just at install.
- Resolve the binary with `which claude` rather than assuming a path. A stale package copy elsewhere
  on disk produced three confidently-wrong conclusions during research.
