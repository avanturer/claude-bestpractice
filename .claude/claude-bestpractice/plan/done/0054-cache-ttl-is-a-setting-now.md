---
id: 0054
title: Cache TTL is a setting now, not a funding-mode verdict
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: docs/ECONOMICS.md, policy/managed-settings.example.json
source: 
done_when: ECONOMICS states what 2.1.243 made configurable, and the policy template carries the keys
blocker: 
after: 
with: 
created_at: 2026-08-27T08:56:27Z
updated_at: 2026-08-27T09:32:11Z
---

docs/ECONOMICS.md says credits 'silently drop' the prompt cache to five minutes and advises stopping background subagents. Claude Code 2.1.243 added promptCacheTtl and subagentPromptCacheTtl 'so API-key and cloud-provider users can keep a 1-hour prompt cache on the main conversation while subagents stay at 5 minutes'. Same release added modelPricing (contracted rates for /cost, status line, telemetry) and modelPicker, and moved Sonnet 5's 2/10 per Mtok from promo to list price.
