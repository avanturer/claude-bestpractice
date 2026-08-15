# Product

## What this is
A control plane for building software with several Claude Code sessions running at
once on one repository. It enforces what must hold, keeps the sessions aware of each
other, and refuses to accept "done" without evidence.

## Who it is for
One person who builds products almost entirely through agents, runs three to eight
sessions in parallel, and reads almost none of the resulting code.

## Non-goals
- **Not a memory engine.** The harness already stores and loads memory. This owns
  curation, not storage.
- **Not a code reviewer.** Four first-party review paths already exist; this one reviews a
  commit at the moment it is made and leaves the others alone. Since 2.1.215 the harness
  no longer runs `/verify` or `/code-review` unasked, so nothing has to be disabled.
- **Not a task manager, and never a second list.** The ledger is the only board, and it
  works because it is load-bearing: the Stop gate refuses a turn whose work no card
  claims, so it cannot drift far from the truth without blocking work. The harness's own
  task tools are NOT integrated, gated or mirrored — they are simply unused, and since
  2.1.233 they are off by default on current models. Measured side by side in one session:
  the ledger held twelve cards with nothing stranded, while the native list still carried
  eight finished six releases earlier and re-injected them on nearly every turn. **A list
  nothing depends on drifts without limit. Never add one.**
- **Not a persuasion layer.** Anything that only asks the model nicely belongs in a
  prompt, not here.
- **Not for teams.** Every design trade-off assumes one owner and no reviewer.
- **Not cross-harness.** The enforcement surface is Claude Code specific, and the
  portable half would be the advisory half, which is the useless half.

## Non-negotiable, on every change
- **Never build beside the plugin what the plugin already has.** Task list, status,
  decisions log, notes — where a mechanism exists, it is the only one. See decision 0005.
- **Every change ships its repair.** The founder upgrades on top of what was working, so a
  fix that only improves the next repository is half a fix. State left behind gets a step
  in `migrate._REPAIRS`.
- **A card before the code.** No file is written until a task on the board says who is
  doing what; the board is how the other sessions decide what is safe to touch.

## Hard constraints
- Standard library only. These hooks run on every tool call; a dependency is latency,
  a failure mode, and a supply-chain surface.
- Total always-on context under 400 tokens. The cost is itemised in the user's own
  usage view while the benefit is counterfactual and invisible.
- No daemon, no vector store, no graph database, no second model watching the first.
- A human with root can always disable everything, and should be able to.

## Current priority
Provenance-linked staleness: stamp every persisted claim with the paths and blob SHAs
it was derived from, and suppress claims whose subjects have moved.
