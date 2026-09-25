---
title: One `+merge` accepts every pull request its chat has open
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/bin/prompt-capture, plugin/bin/pre-tool
date: 2026-09-25
---

## Decision
**`+merge` names the pull requests the session it was said to has open at that moment, and
allows each of them once.** Merged in any order, from any tree of that session. A pull request
opened after the word is not among them, and neither is a sibling session's or one this plugin
never saw opened.

**With none open, it is what decision 0010 made it:** one merge, of the work just accepted,
which the session then opens, checks and merges by itself.

Decision 0010 still holds in every other part: the word is the founder's, read from their own
turn, spent on use, and never implied by `+release` or `+migration`, which stay one action each.

## Why
> неудобно стало когда модель делает 10 исправлений pr и тд и они все готовы и для каждого
> отдельным сообщением в чат надо писать +merge, нужно весь пул освобождать готовый этой фразой

One word per merge made the founder repeat themselves ten times about work they had already
looked at, in the one chat where it had all been shown to them. The protection the repetition
bought was that each word landed on one merge. Naming the pull requests keeps that, because
the word still lands on a known set and nothing past it.

## Rejected
- **Every open pull request in the clone.** The word is typed into one chat. A sibling told
  "не мержи" would have its pull request merged by a word said to someone else: #192, with
  the reach of the whole clone.
- **The pool and one merge more.** It keeps every merge the old word allowed. It also lets a
  pull request opened after the word, which nobody showed the founder, merge on it.
- **A time window.** "Everything merged in the next hour" is a standing grant with a timer,
  and a session working toward a goal can open and merge a great deal in an hour.
- **Freezing each pull request's head at the word.** Exact, and it re-asks the founder
  after every base merge or CI fix pushed before the merge. That is the second question
  decision 0010 exists to remove.

## Cost accepted
A pull request that was open but unfinished when the founder spoke is covered once it is
finished. The merge gate still judges it at merge time, as it judged the single merge before.
A merge of a pull request nobody opened here, such as one from the website, needs the word
said while the chat has none of its own open.
