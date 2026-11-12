---
title: The owner's defect reports are filed as they come, and only what this version was seen to do
paths: plugin/lib/claude_bestpractice/defects.py, plugin/bin/claude-bp-report, plugin/bin/session-start
date: 2026-11-12
---

## Decision
**Where this machine's `gh` files as the owner of the reports' repository, a report is filed
the moment it is captured**: an unset `report_defects` means `auto` there, and a capture starts
a detached `claude-bp-report send`. The account is the github.com `user` in gh's `hosts.yml`;
a token in the environment decides the account instead, so nothing is known. Anyone else still
holds reports until they send them; the founder's written value wins either way.

**A report is sent only if it was last seen on the version that runs now**, and nothing is
sent while a newer version is installed on disk or released. A sighting stamps its version,
so a defect still there is sent once it is hit again.

## Why
> надо что бы так же этот репортер не молча копился а как то сам сразу отправлялся я не знаю
> иначе тоже самое будет

Held for a `send` nobody ran, fifty reports piled up for a month on 2.2.0 to 2.14.3 and went in
one burst (#313–#362). Most were about defects hand-filed issues had long had fixed.

## Rejected
- **`auto` for everyone.** Filing posts publicly under the installer's name in a repository
  they do not own; consent is real only where they own both ends.
- **Asking GitHub who `gh` is.** A hook may not wait on the network; `hosts.yml` is local.
- **Sending old reports with their version marked.** They ask for fixes the code may have.

## Cost accepted
A defect hit once and not again after an upgrade is never filed. Two captures at once start two
senders; a lock makes the second file only what the first had not.
