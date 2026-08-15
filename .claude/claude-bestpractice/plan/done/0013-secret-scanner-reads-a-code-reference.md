---
id: 0013
title: Secret scanner reads a code reference as a credential
state: done
owner: 
branch: claude/vscode-plugin-project-management-r34avz
paths: plugin/lib/claude_bestpractice/redact.py
source: 
done_when: max_tokens=args.max_new_tokens passes the scanner and a real key still does not
blocker: 
after: 
with: 
created_at: 2026-08-15T10:32:14Z
updated_at: 2026-08-15T10:49:46Z
---

#138. max_tokens=args.max_new_tokens is flagged assigned-secret. A value that is an unquoted dotted code reference is not a literal and cannot be a credential.
