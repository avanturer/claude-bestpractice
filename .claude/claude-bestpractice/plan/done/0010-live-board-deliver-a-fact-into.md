---
id: 0010
title: Live board: deliver a fact into a running session
state: done
owner: 
branch: claude/vscode-plugin-project-management-r34avz
paths: 
source: 
done_when: 
blocker: 
after: 
with: 
created_at: 2026-08-14T13:59:57Z
updated_at: 2026-08-14T14:29:18Z
---

The board is injected once at session start (decision 0003), so a running session cannot be told anything new. Claude Code 2.1.224+ binds a per-session unix inbox socket and exports its path and token to hooks before any hook runs. A session writes a note addressed to a peer into Tier B; the peer's own pre-tool hook drains it into its own socket. Four facts only: lease refusal, stale baseline, red suite on a held path, unblocked card.
