---
id: 0060
title: Delivery closes the card, and work never starts without one
state: doing
owner: c2754cd4-e531-5c97-8fdf-92002d2b17e7-540300b0
branch: claude/board-task-closure-rules-qfhva4
paths: plugin/lib/claude_bestpractice/plan.py, plugin/bin/pre-tool, plugin/bin/evidence-gate, tests/test_plan.py
source: 
done_when: a merge closes the cards it delivered, a landed card is closed at Stop, a finish with everything delivered and cards sti
blocker: 
after: 
with: 
created_at: 2026-08-27T22:30:13Z
updated_at: 2026-08-27T22:30:17Z
---

The ledger has no closing half at all: plan.complete has exactly one caller, the CLI. Nothing closes a card and nothing demands it, so a delivered card sits in doing forever - card 0050 has since 2026-08-23. And the opening demand only reads path-shaped write targets, so git commit/merge/rebase/revert/push and gh pr create/merge do real work with no card at all.
