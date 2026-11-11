---
title: What sessions spend is measured from their transcripts, and this repository's context has a ceiling
paths: plugin/lib/claude_bestpractice/spend.py
date: 2026-11-11
---

## Decision
**`claude-bp spend` says what this repository's sessions spent**, read off the transcripts on
the machine: by kind (cache read, cache write, output), by how full the context was on the
request, and what the same conversations would have cost compacted at 200k, 300k and 400k,
with the replay's own check beside it. It names the ceiling in effect and where it is set.

**This repository's sessions compact at 300,000 tokens** (`autoCompactWindow` in
`.claude/settings.json`). The plugin sets no ceiling anywhere else: the setting is the
founder's, and the command says where it goes.

**A session's older checkpoints go when it writes a new one**, and any left for a month.

## Why
> Опередели сам как без потери качества и удобства сделать кратную экономию токенов

Claude Code compacts a model with a million-token window near 967,000, and every request
reads the whole conversation again. In the session that wrote 2.13.0: 271 requests, a mean
context of 478,000, 73% of the spend on re-reading it from the cache and 64% on requests
past 400,000. Replayed turn by turn, a ceiling of 300,000 leaves 58% of that spend over six
compactions, and 200,000 leaves 55% over fourteen. Output was 11%. The nine agents sent to
audit the plugin before it, 437 requests of their own, keep 82% under the same ceiling.

## Rejected
- **Writing `autoCompactWindow` into the founder's settings.** Decision 0008: the plugin
  writes facts there, and this is a preference, one line long.
- **200,000.** Three points cheaper on the measured session, for twice the compactions.
- **A lower effort or a cheaper model.** It buys a part of that 11% with the quality that
  was asked to be kept.
- **The numbers on the board.** The board is sent to the model and has 400 tokens.

## Cost accepted
A session here compacts several times where it compacted once: what carries the work across
is the summary, the checkpoint, the card and what is read again. The replay is a model, and
prints how far it is from what was spent. A directory beside the checkout whose name begins
with the checkout's is counted with it.
