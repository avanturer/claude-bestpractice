---
id: 0052
title: Gate output is bounded in bytes, not only in lines
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: plugin/lib/claude_bestpractice/evidence.py, tests/test_evidence.py
source: 
done_when: a single megabyte-long suite line cannot leave the tail over the documented per-failure budget, proven by a test
blocker: 
after: 
with: 
created_at: 2026-08-27T08:56:12Z
updated_at: 2026-08-27T09:32:11Z
---

run_suite caps the tail at 25 LINES and nothing else, so one long line (base64, a wide assert diff) makes the block reason unbounded. ECONOMICS.md already declares 'Per gate failure <= 500 tokens' — that budget is currently held by an assumption about line length. Claude Code 2.1.247 fixed the harness side ('a hook that printed megabytes of error output being able to overflow the conversation and wedge the session on Prompt is too long'), which removes the wedge but not our own overrun. Cap per line and overall, keep the end.
