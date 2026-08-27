---
id: 0056
title: A line the shell will not parse is not vouched for
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: plugin/lib/claude_bestpractice/shellcmd.py, plugin/lib/claude_bestpractice/vouch.py, tests/test_vouch.py, tests/test_shellcmd.py
source: 
done_when: vouch refuses every line bash -n rejects, and a valid trailing semicolon still vouches
blocker: 
after: 
with: 
created_at: 2026-08-27T10:27:46Z
updated_at: 2026-08-27T10:39:03Z
---

shellcmd.segments drops a dangling && || or | silently, so 'make test &&' parses identically to 'make test' and vouch.for_bash approves it. allow_tool ENDS the permission pipeline, so this overrides Claude Code 2.1.246's 'always require approval for malformed commands with a dangling && or ||'. Measured: bash -n rejects 'make test &&', 'make test ||', 'make test |'; vouch approved all three. It also contradicts vouch's own stated rule — 'The line, whole. Anything this cannot account for ends the vouch for the entire line.' A line the shell cannot parse is the definition of that. Trailing ';' is VALID bash and must keep vouching.
