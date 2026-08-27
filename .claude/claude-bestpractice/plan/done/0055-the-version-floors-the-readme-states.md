---
id: 0055
title: The version floors the README states are the ones that are actually true
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: README.md, docs/README.ru.md, docs/README.zh.md
source: 
done_when: every stated Claude Code version floor matches a changelog entry, in all three languages
blocker: 
after: 
with: 
created_at: 2026-08-27T08:56:27Z
updated_at: 2026-08-27T09:32:11Z
---

README says the inbox needs 2.1.224+. True on a normal host; in a user namespace or rootless container the channel was silently dead from 2.1.232 until 2.1.243 fixed it. The worktree discipline wants 2.1.246, which stopped the background retention sweep removing trees under .claude/worktrees/ that the founder created. And 'validate --strict passes against 2.1.220' predates 2.1.246 fixing /reload-plugins reporting 0 skills for exactly our skills/*/SKILL.md layout.
