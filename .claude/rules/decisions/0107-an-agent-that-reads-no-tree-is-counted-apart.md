---
title: An agent that reads no tree is counted apart from the fan-out ceiling
paths: plugin/lib/claude_bestpractice/subagents.py, plugin/lib/claude_bestpractice/sessions.py, plugin/bin/pre-tool, plugin/bin/prompt-capture, plugin/agents/web-researcher.md, plugin/lib/claude_bestpractice/config.py
date: 2026-11-21
---

## Decision
**A spawn of an agent whose own definition lists its tools, each a web search or page fetch**
(WebSearch, WebFetch, the search servers' tools `search.judged` names), **is counted against
`research_fanout`, eight by default, and not against `subagent_fanout`.** Its own two fields on
the session record, reset with the others at the founder's message. A built-in agent, a
definition with no `tools:` and one with any tool that reads files count as before. The plugin
ships such an agent, `bestpractice:web-researcher`, and the tree ceiling's refusal names it.
Decision 0015 holds for every other spawn.

## Why
> Независимые веб-исследовательские агенты не упираются в потолок, рассчитанный на обход одного
> репозитория (отдельный, более высокий предел или исключение).

Six `general-purpose` researchers of one turn, six subjects, nothing but the web to read: three
were refused as agents "searching the same tree" (#378). The shell had wandered into a clone,
so the spawn was judged by that clone's ceiling of three; the agents read none of it.

## Rejected
- **Reading the prompt for "web research".** Decision 0015's reason: a gate cannot tell a true
  reason from a plausible one.
- **No ceiling for them.** Each still pays for its own context and searches.
- **Counting by where the session stands.** Outside every repository the gate does not run at
  all, and inside one an agent of any kind may read it.

## Cost accepted
A `general-purpose` agent sent to the web is counted with the tree's; the refusal says which
agent type is not. One more agent definition, about 40 always-on tokens.
