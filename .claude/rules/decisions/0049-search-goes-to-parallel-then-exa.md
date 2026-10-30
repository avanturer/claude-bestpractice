---
title: Search goes to the plugin's Parallel first, Exa next, WebSearch last
paths: plugin/.mcp.json, plugin/hooks/bestpractice.ts, plugin/lib/claude_bestpractice/search.py, plugin/bin/pre-tool
date: 2026-10-30
---

## Decision
**The plugin ships Parallel's Search MCP and Exa's** (`plugin/.mcp.json`, keyless), and
answers the harness's WebSearch with Parallel's `web_search`; with Exa's when Parallel returns
nothing; with WebSearch itself only when neither answers. The answer names Exa for what
Parallel's results miss.

**Exa waits for Parallel.** A call to Exa's search from a loop that has not searched through
Parallel in fifteen minutes is sent back to it. **A query is scanned before it leaves**, through
WebSearch, Parallel or Exa: a credential in it is refused. Parallel's and Exa's tools then run
without a prompt; WebFetch keeps asking per host, as the harness has it.
`route_search off` leaves every search tool as the harness has it.

## Why
> paralles search и exa идут вместе с плагином и exa использовалась под точечные сложные
> запросы с которыми parallel не справился, а вебсерч обычный только по необходимости

Measured side by side on five research questions: Exa answered five, Parallel four and a half.
Parallel is the default because its excerpts usually answer without a page fetch; Exa is better
at describing one page.

## Rejected
- **Refusing WebSearch with a pointer to Parallel.** A round trip per search to teach what a
  rewrite does at no cost.
- **Exa first.** A pinpoint engine on every broad question.
- **Keys in the plugin's config.** Both servers are free without one; a project's own server
  at the same address wins over the plugin's.
- **Parallel's newer `mcp.parallel.ai`.** The same two tools for 156 more always-on tokens, and
  beside a project that connects `search.parallel.ai` already, as Fuddy does, both: +758 there.

## Cost accepted
About 780 tokens of tool names and server instructions in every session of a project that does
not connect these servers itself; none in one that does. Parallel calls this endpoint legacy: if
it goes, search falls through to Exa and WebSearch until a release moves it.
