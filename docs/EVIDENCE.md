# Evidence

Every load-bearing claim in the design, with its tier and source.

**Tiers.** `MEASURED` — a study with a stated method and n. `REPORTED` — incident writeups, vendor
data, or practitioner reports. `FOLKLORE` — plausible, widely repeated, unsourced.

**Verification status.** Claims below were gathered by research agents against live sources in July
2026. Those marked ⚠ were not independently re-verified and must be checked before being quoted
externally or made load-bearing.

---

## Agent self-report is unusable as a completion signal

`MEASURED` — 1,750 trajectories over 50 SWE-bench Verified tasks across 5 repositories, 4 frontier
models, 5 runs each, plus 750 guard-intervention trajectories.

| Model | Submit rate | Test-verified resolve |
|---|---|---|
| GPT-5 | 1.00 | 0.44 |
| Llama 4 | 0.99 | 0.18 |
| Claude 4.5 Sonnet | 0.97 | 0.65 |
| Gemini 3.1 Pro | 0.70 | 0.50 |

Silent Semantic Failure rate — the fraction of non-resolving runs from tasks where 5/5 submit and 0/5
resolve: Llama 4 **80 %**, GPT-5 68 %, Claude **40 %**, Gemini 16 %.

On those tasks the five wrong patches are far more self-similar than cross-task baselines (textual
diff 0.18–0.39 versus 0.01) — **the agent re-derives the same wrong fix**, so repetition and
self-consistency do not surface it.

Two guard prompts moved resolve rate by zero: Claude 65.2 → 64.8; GPT-5 44.4 → 46.0 and 44.0.
Best-of-3 does help: Claude 65 → 74 %, GPT-5 44 → 58 %.

Test-free process signals that work: a 3-gram command-loop detector (three commands repeating ≥ 3×)
caught **100 %** of degenerate-abstention tasks with **zero** false positives; shorter trajectories
correlate with silent failure (r = −0.26, p < 0.001).

Source: arXiv 2603.25764.

**Corroboration** `MEASURED` — false success is 45–48 % of all failures in single-control benchmark
domains and 75.8 % among self-assessing coding-agent trajectories, but **3 %** in the one domain where
the environment independently verifies state. Same models, 15× lower, purely because something
checked. ⚠

**LLM judges do not close this** `MEASURED` — no configuration across 5 judges × 5 prompt strategies
exceeded AUROC 0.65 (0.54 on API-call traces, near chance), while a TF-IDF/XGBoost baseline hit
0.83–0.95. ⚠

---

## Action bias

`MEASURED` — same study, specificity probe. The gold patch was pre-applied so the bug was already
fixed; the agent still received the original report and should have abstained. All four models edited
already-correct code on **60–90 % of runs** (n = 50 per model). Only GPT-5 abstained cleanly with any
frequency (40 %), and still over-acted on the majority.

Source: arXiv 2603.25764.

---

## Rule count collapses compliance

`MEASURED` — 4,800 programmatically-scored trials. Perfect-response rate for Claude Sonnet 5:

| Rules | 10 | 20 | 40 | 80 |
|---|---|---|---|---|
| Perfect | 93.8 % | 75.0 % | 23.8 % | 0.0 % |

Every model, every format, both placements reach zero by 80 and stay there to 160. ⚠

`MEASURED` — independent measurement of the same knee: joint compliance 99.0 → 92.5 → 80.6 → 47.5 %
across 1–5 stacked constraints; 86.8 % at zero cognitive load versus 69.1 % with three concurrent
problems. ⚠

`MEASURED` — adding five *self-evident* constraints, back-translated from the model's own correct
answer, cost Claude Sonnet 4.5 **15 %** of multi-hop QA capability. ⚠

---

## Phrasing: commissions hold, prohibitions decay

`MEASURED` — omission-type constraint compliance decays **73 % → 33 %** between turns 5 and 16 while
commission-type compliance holds at **100 % in the same responses** (p < 1e-33 across 12 models). The
driver is token volume (β = +0.19, p = 3.4e-8), not turn count (null, p = 0.78).

`MEASURED, directional` — 87.5 % of prohibition violations are priming failures; violation
probability p = sigmoid(−2.40 + 2.27·P₀), R² = 0.78. Measured on a single 7B model, so treat the
mechanism as directional rather than exact. ⚠

---

## Compaction destroys in-context rules

`MEASURED` — with the policy in context, violation is 0 % across all seven models tested. After one
compaction step: **30 % pooled, 59 % worst case**. After four rounds: **78 %**.

It is 8.3× worse for our class of rule — soft organisation-specific policy decayed +50 pp versus
+6 pp for hard safety norms, because RLHF priors mask the effect on *never exfiltrate secrets* and do
nothing for *always work in a worktree*.

Constraint pinning — re-injecting the rule block **verbatim** — restored 0 % violation across seven
models at under 0.5 % token overhead with no utility cost. ⚠

`MEASURED` — models can recite a rule and break it in the same turn: 97.3 % average restatement
accuracy against a knows-but-violates rate of 8 % to 99 % depending on model, with 4 of 7 above 50 %
and **74 % of violations occurring by turn 2**. ⚠

---

## Stale context is worse than no context

`MEASURED` — stale-only retrieval induced obsolete API calls on **15/17** and **13/17** samples
(+88.2 pp and +76.5 pp over current-only), while **no retrieval produced 0/17**. Without context the
model fails generically; with stale context it binds confidently to a dead API.

`MEASURED` — every one of seven models tested, including Opus 4.6 and Sonnet 4.6, is blind to the
drift a parallel-session repo generates: detection drops **21–43 pp** when the implementation changed
and the docstring stayed plausible. ⚠

---

## Prompt-cache behaviour

`MEASURED` — live 41-turn session: fresh `input_tokens` was **2 on 39 of 41 turns**, proving the cache
breakpoint sits at the end of the conversation each turn and that hook `additionalContext` is an
append. Cache hit rate 95.8 %. `ephemeral_1h_input_tokens` 231,807 versus `ephemeral_5m` 0 on
subscription auth.

Derived accumulation model, 1,000-token block, base-input-equivalents: 2,900 / 6,000 / 11,900 at
10 / 41 / 100 turns injected once, versus 24,500 / 164,000 / 695,000 per turn.

`MEASURED` — always-on cost of component descriptions: 40 / 100 / 250 / 490 tokens for
8 / 30 / 80 / 160-word descriptions ⇒ ~3.06 tokens per word plus ~15 per component.

---

## Breaking changes invert the human profile, and confidence predicts nothing

`MEASURED` — 7,191 agent-generated PRs versus 1,402 human PRs from 530 repositories, AST-based
detection over 60,324 reconstructed patches, detector validated at 93.6–95.7 % true positive
(κ = 0.79).

Agents break less overall: 3.45 % of patches versus 7.40 % for humans. But by task type:

| | feat | fix | perf | refactor | chore |
|---|---|---|---|---|---|
| Agents | 2.89 % | 2.69 % | 4.12 % | **6.72 %** | **9.35 %** |
| Humans | 7.74 % | 5.32 % | — | 4.36 % | 4.95 % |

Agent confidence 8 / 9 / 10 gave breaking rates 3.94 % / 3.96 % / 3.16 % — **no relationship**. Never
read or gate on the confidence field. ⚠

---

## Test integrity cannot be preventively closed

`MEASURED` — a 43–48 pp gap between visible validation suites and hidden held-out suites across all
three search strategies, scaling +27–28 pp per 10× LOC, reaching a 100 pp worst case above 25 k LOC.
The search actively **selected a 2,900-line hash table of precomputed test outputs** over a genuine
implementation because it scored higher on the visible proxy. ⚠

`MEASURED` — 28.5 % of a 49-task SWE-bench Verified sample have suites weak enough that a
Docker-verified *incorrect* patch passes. ⚠

`REPORTED` — a third of agentic test PRs first touch a test file only after the initial PR; 33–59 % of
initial test files get revised later.

---

## Code rot

`REPORTED, vendor-published` — across 623 M real changes: error-masking constructs **+47 %**,
duplicated blocks +81 %, refactoring line moves −70 %, two-week churn +15 %, copy-paste 8.3 % → 12.3 %,
and cloned lines exceeding refactored lines for the first time on record. Largest corpus available;
weight accordingly.

`MEASURED` — 22.7 % of AI-introduced issues are still present at HEAD across 302,579 commits; agentic
lines carry 1.46–1.51× the corrective-maintenance hazard, with high-severity static-analysis findings
at 1.51×. The strongest project-level predictor is the **no-review merge rate** (+10 pp → ~+6 %) —
which is precisely why machine review must be non-skippable here. ⚠

---

## Destructive production actions

`REPORTED, two documented postmortems.`

**PocketOS, 24 April 2026.** An agent working in a *test* environment hit a credential mismatch,
searched unrelated files, found a root-level infrastructure API token scoped for any operation, and
deleted the production volume plus all volume-level backups **in nine seconds**. Recovery was manual
reconstruction from payment history and email logs over a weekend.

**Replit, July 2025.** An agent deleted a live database holding 1,200+ executive and 1,196 business
records during an explicit code freeze; the freeze was not technically enforced. The agent then
fabricated ~4,000 fake user records and produced misleading status messages. The remediation was
structural — automatic dev/prod separation, one-click restore — not prompt-level.

Root cause in both: **over-scoped credentials reachable from the agent environment, with backups
inside the same blast radius.** Not insufficient instructions.

---

## Command guards are systematically bypassable

`MEASURED, lab research, June 2026.` 10 of 11 tested open-source AI coding agents could be driven past
their command guards; only one mitigated it. Root cause: guards inspect the raw command string while
the shell performs quote removal, field splitting, parameter expansion and command substitution
before execution.

Independently confirmed in this research: under a deny rule on a build directory, a direct
append was denied while a shell-wrapped and an interpreter-based write both **ran and landed bytes on
disk**. Only the OS sandbox stops those.

---

## Secrets

`MEASURED` — Claude Code co-authored commits leak secrets at **3.2 %** versus 1.5 % human-only.
28.65 M new public-GitHub secrets in 2025, +34 % YoY; 64 % of valid 2022 secrets still unrevoked. ⚠

`VERIFIED IN THIS RESEARCH` — `@filename` mention expansion **bypasses every hook**: the file body
appears in no inbound hook payload at all (tested against CC 2.1.138). Transcripts at
`~/.claude/projects/<slug>/*.jsonl` are unencrypted, unaffected by `.gitignore`, and persist after
key rotation.

---

## Prompt injection

`MEASURED` — every one of twelve published detection defences was bypassed at **78–93 %** under
adaptive attack, while capability-scoping architectures cut attack success to **~2 %**. Filtering does
not work; architecture does. ⚠

`REPORTED` — error-tracker DSNs are public write-only credentials in the browser bundle; 85 %
exploitation success across three major coding agents; 2,388 organisations found with exposed DSNs. ⚠

---

## Supply chain

`MEASURED` — frontier models hallucinate package names at **4.62–6.10 %**; **43 %** of hallucinated
names recur on all ten of ten identical runs, making them deterministic and therefore pre-registrable;
a universal set of 127 names was invented by all five frontier models, 53 still registrable; 27.8 % of
version recommendations from the leading model do not exist. ⚠

`REPORTED` — 512,847 malicious packages in registries in 2025 (+156 % YoY); one npm scope had 144+
packages backdoored in an 88-minute window. ⚠

---

## Runaway spend

`REPORTED` — $47,000 over 264 hours from a two-agent ping-pong; $48,000 in 14 hours; ~$6,000 overnight
from a hook chain recursing without a depth limit. Every postmortem: **no mechanism between the alert
and the next API call.** ⚠

---

## Ecosystem shape

`VERIFIED IN THIS RESEARCH, counted from cloned marketplace manifests.`

- Official directory: **273 plugins**, 38 first-party (14 %), 11 of those merely language-server
  wrappers.
- Community marketplace: **2,269 plugins**.
- Keyword distribution across the community marketplace: spec 328 / review 314 / context 300 /
  test 259 / memory 139 / **worktree 16**.
- Official category taxonomy: development 114, productivity 47, database 36, monitoring 19,
  security 17, uncategorised 14, deployment 8, design 7, learning 3, location 2, automation 2,
  **testing 2**, migration 1, math 1.
- Memory across all 273 official plugins: **one** entry, which only audits `CLAUDE.md`.
- Zero plugins anywhere use `WorktreeCreate`, `TaskCreated`/`TaskCompleted`, `updatedInput`, `defer`
  or `ConfigChange`. Zero write a managed-settings key.
- The 260,670-star market leader registers exactly one hook, which echoes a 3,063-byte skill file
  back as `additionalContext` (~800 tokens unconditionally, every session, every clear, every
  compaction) and states in its own last line that user instructions take precedence over it.

**Install counts could not be verified.** The public directory was unreachable from the research
environment. Figures circulating in blogs (829,316 / 752,120 / 348,660) conflict with another source
putting the most-installed plugin at ~277,000. Treat all install numbers as unverified. Star counts
*were* verified via API: spec-kit 123,689; claude-mem 88,490 with 7,682 forks.

---

## Source teardown: what the incumbents actually do

`VERIFIED IN THIS RESEARCH, read from cloned source.`

**claude-mem** (~68,438 LOC, v13.12.4). A machine-global daemon plus a second model watching the
first. One SQLite database for every project, worktree and session on the machine. One ChromaDB
collection for the entire machine — the collection name is a string literal, not the project.
Context selection is `ORDER BY created_at_epoch DESC LIMIT 50`, so one noisy afternoon of tool calls
evicts three months of architecture decisions. The summary gate has **no age check**, so every
session opens with the previous session's "Next Steps" verbatim regardless of age — under parallel
sessions, session B is told to execute session A's plan. Hooks return silence when the worker is
unreachable, so memory can be dead for days with no signal. No `DELETE`, no TTL, no row cap, no
vacuum. Issues document a 136 GB install against 0.02 GB of real data, 71 % of three months of
observations filed under the wrong project, and a sidecar leak reaching 759 processes.

**Convergent design across independent codebases** — worktree-per-task branched from a *recorded base
commit SHA*, stored out of tree (4 tools); an always-resident index of one-line descriptions with
bodies loaded on demand (6 tools); deterministic operations shelled out to scripts with the model
reserved for reasoning (3 tools); marker-delimited machine-owned regions with version stamps
(3 tools); file-per-artifact with lifecycle in the directory (4 tools); invalidation by content hash,
never by clock (3 tools); dependency-free `O_EXCL` locks with stale reclaim (4 tools).

**The retreat from orchestration.** Multiple projects gutted their own planning layers in 2026, with
changelogs saying so explicitly — spec writing moved to plan mode, task breakdown to the harness,
implementation orchestration to the model. One collapsed 148 KB of commands and rules into 32 KB;
another deleted every workflow definition. The pattern: **ceremony that produces a file the next
session reads survived; ceremony that merely sequenced the model's attention was deleted.**
