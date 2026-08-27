---
id: 0053
title: The inbox prefix stops competing with the harness's own sender line
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: plugin/lib/claude_bestpractice/inbox.py, plugin/bin/prompt-capture, tests/test_inbox.py
source: 
done_when: a delivered fact reads in a collapsed one-line preview, and a note sent by an older sibling is still recognised as this
blocker: 
after: 
with: 
created_at: 2026-08-27T08:56:26Z
updated_at: 2026-08-27T09:32:11Z
---

Claude Code 2.1.247 collapses cross-session messages to 'Message from @<sender>: <first line>' by default. The harness now names the sender itself, so PREFIX='[claude-bestpractice]' is 22 columns of duplicate in the one line the founder actually reads. Shorten it, and keep the old spelling in prompt-capture's _OUR_VOICES: during an upgrade an older sibling session still delivers with the long prefix, and an unrecognised note becomes the recipient's task statement (the defect of #106/#118/#166).
