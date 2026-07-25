# Roadmap

Ordered by pain × evidence, not by feature appeal. Each stage is independently useful; nothing later
is required for anything earlier to pay off.

## Where this actually stands

**Shipped and verified.** Every item below is implemented, wired and covered:
**345 tests, 20 doctor checks, `make check` green on Python 3.9 / 3.11 / 3.13**, and
`claude plugin validate --strict` passing against the installed CLI. One command installs it into any
repository, and the installer refuses to register the plugin if the doctor fails.

What remains is not code. The gate-metrics file has to accumulate real fire counts before any gate can
be judged worth keeping, and FOLKLORE-tier rules expire on their own after ninety days unless a logged
incident renews them. Both mechanisms exist; both need calendar time.

### Bugs the tests caught that reading the code did not

Eleven, each of which would have shipped something that silently did nothing:

1. **Recording the hook's own pid.** A hook exits milliseconds after it runs, so every session was
   marked dead almost immediately and the next one reaped it. The board would always have been empty.
2. **The plugin's own untracked state counted as changes**, so the gate demanded a test run to justify
   its own bookkeeping — and a gate that fires on noise is one the agent learns to route around.
3. **`git diff` does not show untracked files**, so the reviewer skipped exactly the files an agent
   most often creates.
4. **Multi-line patterns applied line by line can never match.** The swallowed-exception check — the
   single highest-prevalence measured regression — was structurally incapable of firing.
5. **A word boundary after a comma never matches.** `\bno,\b` silently killed the correction detector.
6. **The same bug in the secret scanner**, where `SYSTEM:` never matched.
7. **The documentation gate scanned its own install directory** instead of the repository it ran in.
8. **It also flagged `# TODO` inside string literals**, because it read raw lines instead of comment
   tokens.
9. **A ratchet seeded at zero can never be satisfied** by an existing codebase, so the first run has to
   establish the baseline. Seeded at zero it would have been disabled on day one.
10. **The ratchet skipped the empty case**, so deleting the offending code silently preserved its
    allowance and the budget stopped reflecting reality.
11. **The injection-detector was run against joined fields**, moving every payload off column zero
    where its line-anchored pattern stopped matching — a silent detector reads as "nothing found".

None is visible by inspection. All eleven are why the doctor proves gates by attempting the bad thing,
and why the slop checker is run against this repository's own source.

---

## V0 — Prove the ground before building on it ✅

Four days of measurement, because three claims in the design are load-bearing and were derived rather
than observed on *this* machine.

1. **Doctor harness.** A command that attempts a known-bad action in a scratch directory and asserts
   the denial, for every gate. Config-readback cannot detect a semantics change. This becomes the
   permanent regression suite for every CLI upgrade.
2. **Verify the hook contract locally.** Confirm exit-code semantics, the injection cap, matcher
   behaviour, and which events fire inside subagents — against `which claude`, not a package copy
   elsewhere on disk.
3. **Measure the baseline.** Session startup tokens, cache hit rate, per-turn cost with and without a
   test injection. The economics in [`ECONOMICS.md`](ECONOMICS.md) should be reproduced, not
   trusted.
4. **Instrument before enforcing.** Turn on local telemetry and log every tool decision for a week of
   normal work. Without this there is no way to tell later whether a gate earned its place.

**Exit criterion:** the doctor passes, and the token baseline is written down.

---

## V1 — The spine ✅

Three hooks and a substrate. Nothing else. This already beats the incumbent for this operating mode.

**1. The evidence gate.** Stop and subagent-stop hooks that discard the agent's prose and accept
completion only on a machine-readable test artifact that exists, is newer than the newest file in the
diff, and passes when re-run from a clean checkout in a separate process. Companions: the 3-gram
command-loop detector and the short-trajectory flag. Escalate the reason across attempts and switch
to a durable failure marker before the platform's 8-block ceiling.

*If only one thing ever ships, it is this.*

**2. Scope-drift block.** SessionStart records the verbatim task statement; Stop computes
touched-files minus task-referenced-files and blocks on a non-empty difference. Under 100 ms, zero
tokens, and simultaneously the cheapest protection against parallel sessions overwriting each other.

**3. The session board.** SessionStart reaps dead sessions, registers this one, and injects a
hard-capped ~800-token block: who else is running, on what branch and worktree, what they last
touched, which leases they hold; the 3–5 open items scoped to this repository and branch; this
session's baseline commit; and a one-line health footer. Fenced as data with a provenance banner.

**Substrate.** Tier A committed file-per-artifact with directory-encoded lifecycle; Tier B in the git
common directory with atomic writes, `O_EXCL` locks, TTL leases and a reaper. Plus
`rm -rf <tier-b> && reindex` as a tested command.

**Policy floor.** Managed settings carrying: the deny set for self-tamper paths, the sandbox with
`failIfUnavailable`, the nag suppression, auto-memory off, spawn-depth and concurrency fuses, and the
short `ask` list. Real git hooks under a root-owned hooks path — the repo layer, which is what
survives the plugin being uninstalled or bypassed.

**Hard fuses.** Wall-clock watchdog, tool-call ceiling, repeat-signature deny, hook depth ≤ 2, and no
hook may invoke the agent.

**Why this already wins:** no daemon, no vector store, no second model observing the first, no
unbounded disk, no silent recall horizon, no previous-session plan contamination — and cross-session
visibility, which the incumbent architecturally cannot produce because its read path excludes sibling
worktrees.

---

## V2 — Understanding and provenance ✅ (mostly)

**4. The knowledge layer.** ✅ Four always-on files under 10,400 bytes, with `entities.yaml` as the
centrepiece and its `code:` anchor checked against the tree, so a rename fails validation instead of
leaving the layer describing a ghost. Non-goals and entities injected verbatim at subagent start,
because Explore and Plan agents inherit no project rules and there is no setting that changes that.
**Still to do:** auto-drafting records from transcript correction markers.

**5. Provenance staleness.** ✅ Every claim carries `subject_paths[]` with git **blob** hashes, not
mtimes — creating a worktree or switching branches resets every mtime in the tree, which this
workflow does constantly, and a content hash survives it while still catching a one-character edit.
Drifted claims are marked SUSPECT, suppressed from injection, and counted in the health line.
Suppressed rather than deleted: a claim whose subject moved is usually still mostly right, and a
system that silently drops knowledge is one nobody can debug. **Still to do:** the squash-merge and
force-push cases.

**6. The LLM-first documentation standard.** ✅ Signature-versus-docstring drift, banned derivable
forms, narration and commented-out code, unqualified TODOs. Pure AST plus git, no model. Defaults are
deliberately excluded from the signature hash: flagging them would fire on every tuning tweak and
train the author to write filler. **Still to do:** reference-integrity over paths named in prose.

**7. File leases with real enforcement.** ✅ Pre-tool gate consulting the lease table, denying with
the owning session's id and branch, released on every allow path, with TTL and takeover from a dead
owner. **Still to do:** the burst-counter and silence-window manners so a denied session is nudged
once rather than repeatedly.

---

## V3 — Autonomy ✅

**8. Background review via async rewake.** ✅ Wired to `PreToolUse` with `if: Bash(git commit:*)` and
`asyncRewake`, so it runs detached and wakes the agent only when there is something to say. Moving git
baseline from `git stash create`; findings deduplicated on (path, category) rather than on text,
because diff context drifts and exact matching lets one finding re-accumulate as new every turn;
baseline-subtracted so rewriting a file that already had a problem is not blamed on the agent; and a
**bounded summary plus a file path, never the report**. Deterministic, no model — no LLM-judge
configuration across five judges and five prompt strategies beat AUROC 0.65.

**9. The rigor engine.** ✅ Stage inferred from measured repo signals, ratcheting only tighter.
Migration gating and production-promotion denial switch themselves on at traction; a bare prototype
gets neither. The override is a typed token in the migration body, not a config flag — a flag gets set
once and permanently disables the gate, which is how these die.

**10. The production airlock.** ✅ Out-of-band ingester, never an MCP tool inside a session. Every
attacker-influenceable field fenced with a dynamically-sized fence the payload cannot close, control
and zero-width characters stripped, secrets scrubbed, stack frames resolved to repo-relative paths,
six required fields with `DEGRADED` naming what is missing, and `QUARANTINED` for imperative language.
Filtering is explicitly not the defence: twelve published detection defences were bypassed at 78-93%
under adaptive attack, while capability scoping cut attack success to about 2%.

**11. The ranked repository map.** ✅ Symbol extraction (exact via AST for Python, regex elsewhere), a
dependency graph weighted so a symbol defined everywhere creates no edges, PageRank with dangling-node
redistribution, personalisation toward the task's identifiers, and binary-search budget fitting.
Content-hash cached, never mtime — creating a worktree resets every mtime and this workflow does that
constantly. Given to subagents only once a repository exceeds forty files, because below that a
subagent can just look.

**12. Decision drafting.** ✅ The Stop gate scans the turn's user messages for correction markers — the
moments a human overruled the agent, which is the only provably non-derivable content in a session —
and files pre-written drafts. Accepting is one command. Nothing is auto-accepted: the layer is worth
its tokens only if it is true.

---

## Never build

Each of these is something a surveyed project built and regretted, or something the platform already
ships.

| Never | Why |
|---|---|
| An MCP server in v1 or v2 | Always-resident schema tax is real and measured — one framework's MCP tools cost 37.6 k tokens against 374 for its custom agents, roughly 100× |
| A vector store or graph database | Infrastructure the founder must operate; one requires ~15–25 model calls plus 10+ embedding calls per ingested turn and stores nothing git-diffable |
| A persistent second model observing the session | The incumbent's design; bills up to ~8 k input tokens per tool call |
| A daemon of any kind | Fail-open and fail-closed are both unacceptable, and the precedent is a documented multi-day silent outage |
| A memory storage engine | The platform ships storage and loading. Own **curation**, not storage |
| Task state, worktree lifecycle, checkpoints, parallel-session infrastructure | All native. Integrate via hooks; build the dashboard on the documented agent-listing JSON |
| A security reviewer | The official plugin is better than a v1 would be, and duplicating it produces two interruptions for one action |
| Transcript `.jsonl` parsing | Documented as internal and changing between versions |
| A per-turn natural-language reminder | Fails on the evidence and costs 27× more at 41 turns |
| Personas, activation rituals, greetings, numbered menus | ~2 k tokens per session and a mandatory round-trip before any work |
| A PRD → epic → story pipeline | The projects that built it deleted it in 2026 |
| A VS Code extension that hooks the official one | It exports no third-party API. If an editor surface is wanted, it must be an independent extension using stock APIs and reading our own state files |
| Review-config tooling aimed at team services | Read only by the managed service; the local reviewer ignores it |

---

## The open questions

Stated rather than buried, because each could change a decision.

1. **Does the plugin justify itself?** The benefit is counterfactual — conflicts that did not happen.
   The gate-metrics file is the only answer, and it must exist from V1.
2. **Where does the maintenance burden land?** Roughly one breaking change per week on the surfaces
   this depends on. The capability-gated tiers and the doctor are the mitigation; if they are not
   enough, the honest response is to shrink the surface, not to work harder.
3. **What happens when the platform ships the gap?** Cross-session coordination is an open issue on
   the vendor's own tracker. If it lands, the right move is to delete our version and keep the layers
   that remain unoccupied — memory curation, LLM-first documentation, and the rigor engine.
4. **Name.** The marketplace slug is immutable after publication. Decide before the first publish.
