# Economics

The binding constraint on 3–8 parallel sessions is **not dollars**. It is a shared allowance and a
prompt cache.

## 1. The prompt cache — the counterintuitive part

**Injecting a changing block does not break the cache.**

Measured on a live 41-turn session: fresh `input_tokens` was **2 on 39 of 41 turns**. That proves the
cache breakpoint is placed at the *end* of the conversation every turn, and that SessionStart and
UserPromptSubmit `additionalContext` are **appends, not prefix edits**. They invalidate nothing.

So do not contort the design to keep injected content byte-stable.

### The real cost is accumulation, and it is quadratic

A per-turn block is written into cache each turn and then re-read on every subsequent turn — O(T²) —
while a once-at-SessionStart block grows O(T).

For a 1,000-token block, in base-input-equivalents:

| Turns | Injected once | Injected every turn | Penalty |
|---|---|---|---|
| 10 | 2,900 | 24,500 | 8.4× |
| 41 | 6,000 | 164,000 | **27.3×** |
| 100 | 11,900 | 695,000 | **58.4×** |

**Per-turn injection is the single most expensive mistake available to this plugin**, and it gets
worse in exactly the long sessions this operating mode produces.

**Rule:** all injection defaults to SessionStart. UserPromptSubmit injection is capped at 200 tokens
and dropped above it, with the drop logged.

### The cache invariant

> The plugin never touches the system-prompt layer.

All plugin content is delivered via SessionStart `additionalContext`, UserPromptSubmit
`additionalContext`, or a skill body — never via a system-prompt append, an output style, custom tool
definitions, or an MCP server. Corollaries, each a lint or a hook:

- **No bare tool-name deny rules.** A bare name removes the tool from the system prompt and
  invalidates the whole cache mid-session. Always scope the rule.
  *(A bare-name deny is still the right choice in one case — permanently removing a tool the model
  should never see. Pay the invalidation once, at session start, never mid-session.)*
- **Keep MCP tool search enabled** so schemas stay out of the cached prefix and a server reconnect
  cannot nuke it.
- **Pin model and effort once at SessionStart and never change mid-session.** Both are part of the
  cache key; a switch means the next request reads the entire history uncached. Avoid plan-mode
  variants that make every toggle a full rebuild.
- Prefer clearing (free) or rewinding (hits an existing cache entry) over compaction. Never resume
  into a long pre-upgrade session — that is the single most expensive request available.
- **Ship a cache-health check as a first-class feature:** a Stop hook computing
  `cache_read / (cache_read + cache_write + input)` over the last ten turns, warning below 90 %.
  Measured baseline on a real session is 95.8 %.

### Funding mode is detectable for free

Subscription auth silently gets a **one-hour** cache TTL; drawing on usage credits silently drops it
to **five minutes**. The transcript exposes which: on the measured session
`ephemeral_1h_input_tokens` was 231,807 against `ephemeral_5m` at 0.

When five-minute writes start appearing, the founder is spending cash and the reprocessing frequency
of every injected token multiplies by roughly an order of magnitude. Switch to a frugal profile:
suppress optional injection, stop background subagents.

## 2. The token budget

| Component | Cap | Note |
|---|---|---|
| Always-on knowledge layer | **≤ 2,600 tokens / 10,400 chars** | Exactly saturates the documented 10,000-char `additionalContext` cap, so the same payload works as committed rules or as hook injection with no redesign |
| Plugin's own always-on tax (skill and agent `description:` fields) | **≤ 400 tokens** | Measured at ~3.06 tokens per description word plus ~15 per component. A controlled sweep gave 40 / 100 / 250 / 490 always-on tokens for 8 / 30 / 80 / 160-word descriptions |
| Per-skill description | ≤ 40 words | |
| `SKILL.md` body | ≤ 5,000 tokens | The per-skill re-injection cap after compaction. Most important instructions first — truncation keeps the start |
| **Total plugin tax** | **≤ 3,000 tokens/session** | Under half the ~7.5–8 k startup baseline, ~1.5 % of a 200 k window |
| Per turn | ≤ 200 tokens | Turn-specific content only; a shared wrapper measures and drops above the cap |
| Per subagent spawn | ~1,600 tokens | Product non-goals plus entities, injected verbatim. **At 3–8 parallel sessions this is the dominant recurring cost, and it is the actual reason for the 2,600-token ceiling** |
| Per gate failure | ≤ 500 tokens | Budgeted at ~5 failures/session ≈ 2,500 tokens |

Enforce the plugin tax in CI by parsing the always-on line from `claude plugin details`. It is
controllable to the word, so there is no excuse for guessing.

### Why verbatim, not summarised

The one measured decomposition method that passed facts forward scored **41 % Worse** by collapsing
exactly the cross-cutting constraints that matter — non-goals and hard limits. Inject them verbatim.

### Context cost is now visible in the product

Claude Code shows per-plugin context cost at install, a "will install" inventory, and a "not used
recently" list framed around startup and context cost. `/usage` attributes recent usage to skills,
subagents, plugins and individual MCP servers as a percentage of total, and flags anything at 10 % or
more.

A plugin whose benefit is counterfactual — conflicts that did not happen — and whose cost is itemised
by name in the founder's own dashboard has an adverse-visibility problem that value alone does not
solve. The answer is to be small enough that it never appears in that list, and to instrument the
benefit (see §4).

For contrast: one popular framework's `CLAUDE.md` alone is 60,039 bytes ≈ 15,009 tokens loaded
unconditionally every session, shipping 163 skills, 23 commands and 22 agents with zero enforcement.
Another measured a session starting **32 % full (64 k / 200 k) before any user input**, with MCP tools
at 37.6 k (18.8 %) against custom agents at 374 tokens (0.2 %).

**MCP costs roughly 100× what agents cost.** That inverts the usual advice, and it is why the default
bundle ships **zero** MCP servers.

## 3. Rate limits and admission control

The five-hour and weekly windows are **per account and shared with Claude chat and other surfaces**,
so N parallel sessions burn them ~N times faster. At exhaustion Claude Code **blocks** until reset
with no automatic model fallback. A single large fan-out can exhaust the *weekly* allowance before
the five-hour window resets — the specific failure mode of this operating mode.

There is no queueing and no platform backpressure, so the plugin must own admission control.

**Hooks receive no rate-limit fields.** The only programmatic read is the status line, which carries
`rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}`. The status line writes them to a
file that SessionStart reads.

| Weekly usage | Policy |
|---|---|
| < 70 % | Normal |
| ≥ 70 % | Block agent-team spawning; force subagents to the small model |
| ≥ 90 % | One loud warning naming the human-readable reset time |

### Hard fuses

- A wall-clock watchdog started at SessionStart that terminates the session and writes a reason file
  the agent reads at next start.
- A pre-tool counter denying past a per-session tool-call ceiling, and denying any tool+args
  signature repeated more than three times — the same code path as the loop detector in the evidence
  gate.
- **Hook recursion depth ≤ 2**, and a hard rule that **no plugin hook may ever invoke the agent.**
- Subagent spawn depth pinned to 1. Nesting silently defaulted to 3 in a recent release with a
  one-line changelog entry and no migration notice.

Documented burns from missing exactly these fuses: **$47,000 over 264 hours** from a two-agent
ping-pong, **$48,000 in 14 hours**, and **~$6,000 overnight** from a hook chain recursing without a
depth limit. Every postmortem names the same root cause: *no mechanism between the alert and the next
API call.*

**Enforce, never alert.**

### Scheduled work

Background and scheduled jobs run in a dedicated short-lived headless session, never inside a
long-lived interactive one, at intervals of tens of minutes minimum. A scheduled task fires and
re-sends its full context each time — registering one inside a long interactive session means
re-sending a 200 k context per interval.

## 4. Instrument the benefit

The value here is counterfactual, which means it is invisible unless measured. Telemetry already
emits tool-decision events (with decision and source) and permission-mode changes — precisely the two
signals needed to audit whether the founder is being nagged — plus cost, token, commit and PR
metrics, on any plan, with a local exporter. The prompt id appears in both the telemetry attributes
and the status-line payload, so the passive UI joins to telemetry on one key for free.

Do not build on the organisation analytics API: it requires an Admin API key, and the Admin API is
unavailable for individual accounts.

Every gate writes fire count and true-positive count. That file is what justifies keeping the plugin
— and what deletes a gate that never earned its place.
