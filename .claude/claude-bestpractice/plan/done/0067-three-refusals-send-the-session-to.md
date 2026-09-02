---
id: 0067
title: Three refusals send the session to a command that does not exist
state: done
owner: 
branch: claude/skill-state-token-efficiency-ydpwsu
paths: plugin/bin/pre-tool, plugin/bin/claude-bp, plugin/lib/claude_bestpractice/config.py, plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/vouch.py, tests/test_gates.py
source: 
done_when: no user-facing string names a claude-bp command that cannot be run, a test proves that over every string literal the plu
blocker: 
after: 
with: 
created_at: 2026-09-02T19:13:37Z
updated_at: 2026-09-02T19:32:50Z
---

PROTECTED_STATE refuses .claude/claude-bestpractice/config.json and tells the session to change the test command with 'claude-bp ci' (pre-tool:858). 'claude-bp set test_command' refuses and says the same (claude-bp:293). config.py:254 states the rule: 'claude-bp ci owns them because it PROVES the command before it writes it'. All three are false: the spelling is claude-bp-ci, its commands are fixed (status|local|github|off|record-green|green-covers-tree) with no way to set test_command, and ci.py only READS a detected command for the pre-push hook. Proven by running all three. Meanwhile evidence.py:409 tells the reader to set test_command in the very file pre-tool refuses. A session told to make finishing verifiable has no path at all, and every instruction it is handed either errors or is refused. Same defect class as card 0065.
