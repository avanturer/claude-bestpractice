---
id: 0059
title: A case terminator is a line this parser cannot account for
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: plugin/lib/claude_bestpractice/shellcmd.py, tests/test_shellcmd.py
source: 
done_when: a line carrying ;; ;& or ;;& yields nothing to judge, without pushing segments back over the complexity budget
blocker: 
after: 
with: 
created_at: 2026-08-27T10:33:53Z
updated_at: 2026-08-27T14:07:59Z
---

shlex with punctuation_chars hands ';;' back as one ordinary token, so 'a ;; b' reads as command 'a' with arguments ';;' and 'b' while bash rejects the whole line as a syntax error. vouch would therefore approve a line the shell will not run. Impact is small — bash executes nothing either way — and it predates 1.55.0's dangling-operator fix, whose scope was exactly what Claude Code 2.1.246 named. Not widened on a guess. The obstacle is mechanical: the natural fix is one more branch in segments(), which is already at the complexity budget of 10, so it needs a restructure rather than a line.
