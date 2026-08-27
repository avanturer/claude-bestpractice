---
id: 0060
title: Delivery closes the card, and work never starts without one
state: done
owner: 
branch: claude/board-task-closure-rules-qfhva4
paths: plugin/lib/claude_bestpractice/plan.py, plugin/bin/pre-tool, plugin/bin/evidence-gate, tests/test_plan.py
source: 
done_when: a merge closes the cards it delivered, a landed card is closed at Stop, a finish with everything delivered and cards sti
blocker: 
after: 
with: 
created_at: 2026-08-27T22:30:13Z
updated_at: 2026-08-27T23:42:35Z
---

The ledger has no closing half at all: plan.complete has exactly one caller, the CLI. Nothing closes a card and nothing demands it, so a delivered card sits in doing forever - card 0050 has since 2026-08-23. And the opening demand only reads path-shaped write targets, so git commit/merge/rebase/revert/push and gh pr create/merge do real work with no card at all.

[2026-08-27T23:42:35Z] closed on delivery — the merge of claude/board-task-closure-rules-qfhva4 carried plugin/lib/claude_bestpractice/plan.py, plugin/bin/pre-tool, plugin/bin/evidence-gate, tests/test_plan.py.
