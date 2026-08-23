---
title: The founder accepts the work, the session does the rest
paths: plugin/lib/claude_bestpractice/config.py, plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/pre-tool
date: 2026-08-23
supersedes: 
---

## Decision
**Opening a pull request, checking it and merging it is the session's job — once the
founder has accepted the work.** Acceptance is one literal in their own message, `+merge`,
and the session then proceeds on its own without asking again. A green suite is not
acceptance; nobody but the founder can give it.

**Production is a different question and always theirs.** A release or an over-the-air
promotion needs `+release`; a destructive migration needs `+migration`. These are never
implied by `+merge` and never by each other.

**Every literal is spent on use**, so none can become a standing grant, and none can be
written by the session being gated — they are read out of the founder's own turn by
`prompt-capture` and stored where no session can write.

## Why
> сделай следующим и если я правильно понял то я хочу что бы пр не мерджился и тем более ота не катился без вообще моего ведома. Тоесть если мы обсудили идею он ее сделал и я посмотрел и сказал мне все нравится и тд то он сам делает пр сам его проверяет и сам мерджит если нет проблем, вот как я хочу что бы выглядело, а такие вещи как ота или тем более новый билд в релиз всегда должны быть одобрены мной

Before this, the merge gate demanded that a green pull request be merged — including ones
the founder had never looked at, and it repeated the demand every turn (#140). The gate
was pushing work over the line the founder had drawn, in the name of not leaving work
unfinished.

## Rejected
- **Merging on a green suite alone**: green says the tests pass, not that the change is
  wanted. That conflation is exactly what #140 reported.
- **Reading acceptance out of prose**: a gate switched by phrasing. The literal carries no
  language either — `+merge` rather than `merge ok`, because the founder writes Russian and
  «мерджи» opened nothing while the refusal answered by asking them to say it in English
  (#147).
- **A standing approval**: "always merge my PRs" is the same as no gate, one release later.
- **Asking again after acceptance**: the second question is the interruption this plugin
  exists to remove. Accepted once, the session opens, verifies and merges by itself.
