---
id: 0058
title: The interruption budget carries its measurement, not its assumption
state: done
owner: 
branch: claude/plugin-updates-review-xrdhmi
paths: policy/managed-settings.example.json, policy/README.md
source: 
done_when: the ask claim names the version it was measured on, and the silence block covers feedbackDrafts
blocker: 
after: 
with: 
created_at: 2026-08-27T10:27:46Z
updated_at: 2026-08-27T10:39:03Z
---

policy §3 asserts 'permissions.ask is the ONLY mechanism that forces a human prompt in every mode' — an assumption the file's own header says must be verified against the installed binary. 2.1.246 added an auto mode classifier-rules tab and 2.1.247 a one-keystroke 'Yes, and switch to auto mode' from a Bash permission prompt, so the founder reaches auto mode far more easily than when that line was written. Measured on 2.1.247: with an ask rule under --permission-mode auto, Bash was refused; without it, the same call ran. The claim holds; record the measurement and the version. Also add feedbackDrafts (2.1.247) to the silence block beside feedbackSurveyRate.
