---
title: What sessions spend is measured from their transcripts, and this repository's context has a ceiling
paths: plugin/lib/claude_bestpractice/spend.py
date: 2026-11-11
---

## Decision
**`claude-bp spend` says what this repository's sessions spent**, read off the transcripts on
the machine: by kind (cache read, cache write, output), by how full the context was on the
request, and what the same conversations would have cost compacted at 300k, 500k and 800k,
with the replay's own check beside it and what the replay leaves out said under it. It names
the ceiling in effect and where it is set, and recommends none.

**This repository's sessions compact at 850,000 tokens** (`autoCompactWindow` in
`.claude/settings.json`): the large context is used on purpose. The plugin sets no ceiling
anywhere else: the setting is the founder's.

**A session's older checkpoints go when it writes a new one**, and any left for a month.

## Why
> давай вообще автокомпакт поставим в районе 800тыс-900тыс, иначе это бред мы просто не будем
> пользоваться огромным контекстом

Measured on the session that wrote 2.13.0: 271 requests, a mean context of 478,000, 73% of the
spend on re-reading it from the cache. A ceiling of 300,000 replays to 58% of that spend over
six compactions, and was set here for a day. The replay counts neither what a session reads
again after a compaction nor what it loses by working from a summary, and the founder's
answer was that spend on this repository's own sessions is not what matters.

## Rejected
- **A low ceiling (300,000).** Six summaries where there was one, to save a spend nobody
  minds, by a figure that leaves out its own cost.
- **Writing `autoCompactWindow` into the founder's settings.** Decision 0008: the plugin
  writes facts there, and this is a preference.

## Cost accepted
A long session here reads most of a million tokens on every request. The replay is a model,
and prints how far it is from what was spent. A directory beside the checkout whose name
begins with the checkout's is counted with it.
