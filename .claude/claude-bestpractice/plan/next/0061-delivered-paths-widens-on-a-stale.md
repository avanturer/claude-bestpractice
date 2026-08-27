---
id: 0061
title: delivered_paths widens on a stale local base ref, and now that widening closes cards
state: next
owner: 
branch: claude/board-task-closure-rules-qfhva4
paths: plugin/lib/claude_bestpractice/pullrequest.py, plugin/lib/claude_bestpractice/plan.py
source: 
done_when: the delivered set is measured against the ref that is actually the base, or the closure states why the local one is safe
blocker: 
after: 
with: 
created_at: 2026-08-27T23:43:02Z
updated_at: 2026-08-27T23:43:02Z
---

_files_against tries the local ref before origin/<ref> and returns the first non-empty answer. A local main that is behind the remote therefore reports every file changed since the OLD merge base, not since the real one — observed here at 81 files against a 20-file branch, because the fresh clone's local main was 19 commits behind. Every existing caller filters review findings with it, where wider means keeping more findings and errs safe. settle_delivered is the first caller where wider means closing MORE cards, which errs the other way: a card naming a file from an already-merged release closes on somebody else's delivery. Decide between preferring origin/<base> when it resolves, and passing the base the pull-request record already carries all the way down.
