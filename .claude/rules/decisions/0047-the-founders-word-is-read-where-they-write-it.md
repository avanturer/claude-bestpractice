---
title: The founder's word is read where they write it, and not where it is withheld
paths: plugin/lib/claude_bestpractice/config.py, plugin/lib/claude_bestpractice/founder.py
date: 2026-10-30
---

## Decision
**`+merge`, `+release`, `+deploy` and `+migration` count in a sentence of the founder's, as many
as one message carries**, and on a line of their own as before. Each counts in its clause, the
stretch between punctuation and a `но`, `а` or `but`. Retires decision 0010's line-only shape.

**A token does not count** in a clause that refuses, defers or conditions it (`не`, `нет`,
`если`, `когда`, `после`, `потом`, `not`, `don't`, `until`, `once`…), in a sentence ending in `?`,
or in code, quotes, a quoted line or pasted content. Under a message of this plugin's own, only
lines holding nothing but the word count.

## Why
> сделай возможность писать +merge +release одновременно в одном сообщении … и сделай что бы
> +merge воспринимался и в обычном сообщении не отдельным словом отправленым

The line rule asked for a newline before every word, and two words on one line granted neither.
What it existed to stop is still stopped: «пока не вливай, я не говорил +merge» grants nothing (#192).

## Rejected
- **Any token anywhere.** The sentence that withheld a merge in #192 would grant it again.
- **The whole sentence judged at once.** «+merge, но релиз не надо» withheld the merge it gave.
- **Natural-language words for the grant** («мерджи»). Decision 0010 still holds: the symbol
  carries no language, and every form missed is a refusal (#147).

## Cost accepted
A grant written beside a refusal or a condition in one clause («+merge без ревью не надо») is
not read, and is said again. A sentence quoting somebody's `+merge` without quotation marks or
backticks grants it.
