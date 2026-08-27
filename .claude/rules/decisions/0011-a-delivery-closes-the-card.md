---
title: A delivery closes the card it delivered
paths: plugin/lib/claude_bestpractice/plan.py, plugin/bin/pre-tool, plugin/bin/evidence-gate, plugin/lib/claude_bestpractice/gitpolicy.py
date: 2026-08-27
supersedes: 
---

## Decision
**The delivery closes the card, and the plugin does it rather than asking.** A merge this
gate clears closes the cards of the session that made it whose files that merge carried;
at Stop, a branch that adds nothing over its base and holds nothing uncommitted closes
them the same way. Neither is a question put to the founder.

**A finish over delivered work with a card still open is refused**, naming `done` and
`pause`. That is the backstop for every delivery the plugin did not itself perform — a
merge on the website, a branch fast-forwarded by hand.

**Work done entirely through git needs a card like any other.** `git merge`, `rebase`,
`cherry-pick`, `revert`, `am` and `apply` write no file, so every rule reading write
targets saw nothing. `--abort`, `--continue` and `--skip` are never refused.

## Why
> тоесть я не хочу следить за тем что после того как я дал на все добро меня еще
> спрашивают закрыть ли задачи или ждут команды или просто забывают

The ledger had no closing half at all: `plan.complete` had one caller, the CLI. A card
reached `doing` because a gate demanded it and left only if somebody typed a command, and
nobody ever did — this repository's card 0050 sat in `doing` for four days over a release
it had merged and tagged on the first of them. A row claiming work is in flight over work
that shipped is the board asserting a collision that cannot happen, which is the same lie
the reaper and `sweep_idle` exist to stop telling from the other end.

## Rejected
- **Asking the founder.** Decision 0010 already spent that question: `+merge` is their
  word on the work and the session does the rest. Closing the card is the rest.
- **Closing every card the session holds.** A delivery finishes the work it carried, not
  whatever else that session had claimed. Matched on the files the card named.
- **Demanding it and closing nothing.** A demand costs a blocked turn on the ordinary
  path; it is kept only for what the automatic closure could not account for.
- **Refusing `git push` without a card.** The merge before it just closed the cards
  correctly, so the push after it would be refused for that.

## Cost accepted
A merge recorded at PreToolUse that then fails leaves a card closed early. The cost is one
`claude-bp-plan add`, against a board that was wrong about everything it ever delivered.
