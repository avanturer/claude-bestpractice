---
id: 0062
title: A command hidden in a substitution is not a command the gate cannot see
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: plugin/lib/claude_bestpractice/shellcmd.py, tests/test_shellcmd.py, tests/test_pullrequest.py
source: 
done_when: a merge hidden in $(), a backtick or a subshell is refused by the same gate that refuses the plain one
blocker: 
after: 
with: 
created_at: 2026-08-30T10:26:36Z
updated_at: 2026-08-30T10:35:48Z
---

MEASURED against the armed gate: 'gh pr merge 1 --squash' is denied and 'FOO=$(gh pr merge 1) echo hi' is ALLOWED — and bash was shown to execute the substitution. Same for a backtick and for an arithmetic subscript, the shape Claude Code 2.1.251 started requiring approval for. So a session can merge without the founder's +merge, which is precisely what decision 0006 exists to prevent. The cause is the same as the case-terminator one: shlex splits '$(' into tokens and segments() returns a confident but wrong argv whose program position is '(' — so runs() never matches 'gh', and pullrequest._gh_subcommand never reaches its regex fallback, which WOULD have caught it. Quoting is the discriminator and it survives tokenisation: a quoted substitution stays inside one token (#76 stays fixed).
