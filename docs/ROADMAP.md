# Roadmap

Ordered by pain × evidence, not by feature appeal. Each stage is independently useful; nothing later
is required for anything earlier to pay off.

## Where this actually stands

| | |
|---|---|
| **Shipped** | V0 doctor harness · V1 spine (six gates, two-tier substrate, session board, hard fuses) · V2 knowledge layer with checked anchors · V2 provenance staleness · V2 file leases · V2 LLM-first documentation gate |
| **Verified** | 219 tests · 13 doctor checks · `make check` green on Python 3.9 / 3.11 / 3.13 |
| **Remaining** | Auto-drafting decision records from transcripts · background review on commit · the rigor engine's later stages · the production-signal airlock · the repomap |

Two things were caught by tests rather than by review, and both would have broken real use: recording
the hook's own pid marked every session dead within milliseconds so the board was always empty, and
the plugin's own untracked state files made the gate demand a test run to justify its own bookkeeping.
Neither is visible by reading the code.

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

## V3 — Autonomy

**8. Background review via async rewake** on commit, with a moving git baseline so only this turn's
changes are reviewed, findings deduplicated on (path, category), baseline-subtracted so pre-existing
issues are not blamed on the agent, and returning a **bounded summary plus a file path — never the
report**. One project shipped the un-bounded version, watched every forked context inherit a growing
report until sessions froze, and reverted with the post-mortem in the source.

**9. The rigor engine.** Stage inferred from measured repo signals, mapping to which gates fire.
Autonomous, no config, no asking.

**10. The production airlock.** Out-of-band ingester, sanitised fenced signal files, six mandatory
fields, `DEGRADED` on schema failure. Nightly authorization probe from the generated route manifest;
weekly backup restore verification under a credential the agent cannot reach.

**11. The anti-evasion ratchet.** No threshold, budget, allowlist or suppression may move permissively
without a justification trailer; newly added suppression comments are themselves ratcheted down.
Highest-leverage single check for unsupervised multi-session work — and it must be written from
scratch, because the tool this pattern comes from documents the requirement in a comment and does not
machine-enforce it.

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
