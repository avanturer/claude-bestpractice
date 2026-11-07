---
title: A pause keeps the agents it stops, and its card says which to resume
paths: plugin/bin/evidence-gate, plugin/bin/pr-opened, plugin/lib/claude_bestpractice/subagents.py, plugin/lib/claude_bestpractice/plan.py
date: 2026-11-07
---

## Decision
**A turn that ends on a pause set down in it, while an agent of the session still works, is
refused once for that pause**, naming each agent by id and `TaskStop`. **Each agent the session
stops in that turn is written on the paused card**, by its id and what it was sent to do, read off
`TaskStop`'s result, and `claude-bp-plan claim` says them again: a `SendMessage` to the id resumes
the agent with its own context. Agents sent off under a card paused in an earlier turn are other
work, and neither asked about nor written. A shell, a monitor, a workflow or a teammate is neither
asked about nor kept. The note keeps the card's stamp, which the gate reads as the moment it was set
down. Decisions 0070 and 0075 hold otherwise.

## Why
> мне часто надоедает что клод именно стопает агентов полностью и закрывает их когда я прошу
> поставить его всё на паузу, может можно при просьебы паузы что бы он их оставлял?

Offered both, the founder chose «Остановить с возвратом»: stopped, so nothing is spent while the
work waits, and the card saying which agent did what for the session to resume. A session told to
stop had stopped its agents and, told to go on, started new ones that read everything again; one
left running spends tokens and its report wakes the session to carry on. Measured on Claude Code
2.1.319: `TaskStop` answered `task_type: "local_agent"` with the agent's description, and a
`SendMessage` to the stopped agent's id resumed it with what its first prompt had told it.

## Rejected
- **Leaving the agents running under the pause.** The founder's other choice: they spend tokens,
  and each report starts the work again.
- **A `+pause` word.** Decision 0033's reason stands: a pause claims nothing done.
- **Recording the stop at PreToolUse.** Its input is an id alone, and a shell's reads like an agent's.
- **Reading stopped agents off Stop's `background_tasks`.** It lists only work still in flight.

## Cost accepted
A pause over agents still at work costs one more turn. An agent stopped in an earlier turn than
the pause, or with no card paused after it, is on no card.
