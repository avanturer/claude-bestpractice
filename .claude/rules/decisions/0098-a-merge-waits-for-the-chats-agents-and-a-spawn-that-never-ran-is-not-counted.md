---
title: A merge waits for the chat's agents, and a spawn that never ran is not counted
paths: plugin/lib/claude_bestpractice/subagents.py, plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/sessions.py, plugin/bin/pre-tool, plugin/bin/pr-opened
date: 2026-11-12
---

## Decision
**A merge closes none of the cards it carried while an agent the chat sent off has not reported
back**, as the Stop gate closes none then (decision 0070). Each stays in `doing`, written what
was delivered and that an agent is at work, for a later delivery or finish to close. An agent
sent off before the chat's last turn that ended with none at work is not waited on, nor is the
agent that merged. This revises decision 0087, where only the chat's other pull requests and
trees held a card open.

**A spawn whose call came back an error is not counted toward `subagent_fanout`**: no agent
ran. Decision 0015 holds otherwise.

**Both are read off the chat's transcript**: the spawn's call, its result under the call's id,
and the notification an agent in the background ends with.

## Why
> three Agent calls failed with "Agent type 'seo-technical' not found" … and the next three
> valid general-purpose spawns in the same turn were refused

> 1152 was auto-closed 'on delivery' by a merge of other paths while a subagent was still
> working under it

Measured on Claude Code 2.1.322: that spawn fired PreToolUse, then PostToolUseFailure, and its
result came back `is_error`. An agent sent to the background came back `async_launched`, and
ended with a `queue-operation` `enqueue` of a `<task-notification>` naming the call's id. Only
Stop and SubagentStop carry `background_tasks` (#316, #318).

## Rejected
- **PostToolUseFailure giving a spawn back.** A thirteenth hook entry.
- **Holding only the cards whose files an agent writes.** No call says which those will be.
- **Counting from the transcript alone.** It is written behind the conversation: six spawns of
  one message read it before any of them has a result.

## Cost accepted
A failure the transcript does not hold yet still counts, and an agent that ran and then failed
is given back too. After a restart, an agent the old process left is at work until the chat's
next turn ends with none.
