---
title: estimate the window's fill from the transcript file size
outcome: failed
paths: plugin/bin/evidence-gate, plugin/lib/claude_bestpractice/config.py
branch: claude/charming-curie-otqrvy
session: 
recorded_at: 1790029661
---

Measured on a live transcript: 1.27MB over 189 lines for a window around 80k tokens, i.e. ~16 bytes per token, against the ~4 the rule of thumb assumes — a factor-of-four error. The window size itself (200k vs 1M) is not a fact a hook holds either, so the threshold would have been a guess on top of a guess. The session record's own tool_calls is ours, cheap and monotone.
