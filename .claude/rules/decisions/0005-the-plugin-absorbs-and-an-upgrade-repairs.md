---
title: The plugin absorbs what it owns, and an upgrade repairs the repository it lands in
paths: plugin/lib/claude_bestpractice/migrate.py, plugin/bin/pre-tool, plugin/bin/evidence-gate
date: 2026-08-07
---

## Decision
**Nothing the plugin owns is reimplemented beside it.** Task list, status, notes,
decisions log: where a mechanism exists it is the only one, and a session that would build
its own is building a second source of truth.

**An upgrade fixes the repository it lands in.** Every change that leaves state behind
ships a step in `migrate._REPAIRS`, idempotent and recorded once, so an existing
repository reaches the same state as a fresh one.

**Duplicates are absorbed where that is mechanical, refused where it is not.** A scratch
TODO a session wrote as a stand-in is pulled into the ledger and its file rewritten to a
pointer. A registry being created beside a populated ledger is refused at the write.

## Why
`docs/TODO.md` was created mid-session beside a populated ledger, wired into three entry
points and committed twice before a merge conflict made it visible. The founder had asked
for a TODO system "while the plugin does not support it"; it does, and neither noticed
(#103). Reporting is the weaker half — by the time a report is read the duplicate has
readers. And a repository that upgraded kept every workaround it had, because the fixes
only changed what happened next: *"я обновляю плагин на том что работало"*.

## Rejected
- **Adopting curated documents automatically.** Deciding what in one is a task needs
  judgement a regex lacks; rewriting the founder's documents on a hunch is worse than the
  duplicate. Reported, with `adopt --brief`, and left to the agent.
- **Reporting duplicates instead of refusing them.** Tried; it is how #103 happened.
- **A version-keyed migration chain.** Repairs are keyed by what they fix: a repository
  skipping four versions needs the same steps as one skipping a single version.

## Cost accepted
An upgrade writes to the working tree unasked — bounded to files a previous session wrote
as a stand-in, each rewritten to a pointer rather than deleted, so git keeps the text and
anything that linked to it still resolves.
