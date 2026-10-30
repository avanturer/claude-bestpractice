---
title: A migration number the trunk already gave is refused at merge
paths: plugin/lib/claude_bestpractice/migration_numbers.py, plugin/lib/claude_bestpractice/pullrequest.py
date: 2026-10-30
---

## Decision
**A merge is refused while the pull request adds a numbered migration whose number the trunk
holds in another file of the same directory.** The refusal names both files and the next number
free on the trunk and the branch together. A migration is a file under `migrations/`,
`migration/` or `migrate/` whose name starts with its number (`0188_feedback.sql`, `V12__init.sql`,
`0042_add_index.py`); a timestamp is nobody else's number and is not read.

**Asked of the files the pull request adds, against the trunk as fetched**, when the merge is
judged. Where git cannot answer, nothing is refused.

## Why
> так же проверил работу с fuddy в гит и локально нашел и исправил все недочеты и немасштабируемости

A founder's clone held ten migration numbers twice over (0086, 0089, 0099, 0105 to 0109, 0115,
0148). Each pair came from two sessions that took "the next number" from the trunk they started
on; both were right until the second merged, and a runner keyed on the name applied both in an
order nobody chose.

## Rejected
- **Checking when the file is written.** Neither session is wrong then; the clash exists only
  once the other has merged.
- **Renaming the file for the session.** A runner may have applied it under its name already,
  and a gate that rewrites a migration at merge time authors what it judges.
- **Every migration directory in the trunk.** The clashes already merged are history; a gate
  that refuses on them refuses every merge until someone rewrites applied migrations.

## Cost accepted
A clash between two pull requests that are both still open is found only when the second is
merged. Numbering per subdirectory is judged per subdirectory.
