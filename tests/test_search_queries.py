"""A query leaves the machine: nothing of the project's goes with it (decision 0049).

The plugin ships Parallel's and Exa's search servers and sends WebSearch through them. Every
query, whichever tool carries it, is read by whoever runs the engine.
"""

from __future__ import annotations

import json

from helpers import REPO_ROOT, RepoCase, harness_matches

from claude_bestpractice import search

KEY = "sk-ant-api03-" + "a1B2c3D4e5F6g7H8i9J0" * 4
PARALLEL = "mcp__plugin_bestpractice_parallel__web_search"
EXA = "mcp__plugin_bestpractice_exa__web_search_exa"


class TestWhatIsJudged(RepoCase):
    def test_the_search_and_page_tools_of_every_kind_are_judged(self):
        for tool in ("WebSearch", PARALLEL, EXA, "mcp__Parallel_Search__web_fetch",
                     "mcp__Exa__web_fetch_exa", "mcp__exa__web_search_advanced_exa"):
            self.assertTrue(search.judged(tool), tool)
        for tool in ("Bash", "Read", "WebFetch", "mcp__github__search_code", "mcp__x__web_searcher"):
            self.assertFalse(search.judged(tool), tool)

    def test_only_parallel_and_exa_run_unasked(self):
        for tool in (PARALLEL, EXA, "mcp__Parallel_Search__web_search", "mcp__Exa__web_fetch_exa"):
            self.assertTrue(search.vouched(tool), tool)
        for tool in ("WebSearch", "WebFetch", "mcp__tavily__web_search", "mcp__complex__web_search"):
            self.assertFalse(search.vouched(tool), tool)

    def test_the_hook_is_wired_for_every_one_of_them(self):
        hooks = json.loads((REPO_ROOT / "plugin" / "hooks" / "hooks.json").read_text())
        matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
        for tool in ("WebSearch", PARALLEL, EXA, "mcp__Exa__web_fetch_exa"):
            self.assertTrue(harness_matches(matcher, tool), tool)


class TestTheGate(RepoCase):
    def call(self, tool: str, tool_input: dict):
        return self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                          "tool_name": tool, "tool_input": tool_input})

    def test_a_credential_in_a_query_is_refused_whichever_tool_carries_it(self):
        for tool, tool_input in (("WebSearch", {"query": f"why does {KEY} fail"}),
                                 (PARALLEL, {"objective": "x", "search_queries": [f"auth {KEY}"]}),
                                 (EXA, {"query": "error", "objective": f"with key {KEY}"})):
            proc = self.call(tool, tool_input)
            self.assertEqual("deny", self.hook_decision(proc), tool)
            self.assertIn("credential", self.hook_reason(proc))

    def test_a_clean_query_to_parallel_runs_unasked(self):
        proc = self.call(PARALLEL, {"objective": "uv run flags", "search_queries": ["uv run frozen"]})
        self.assertEqual("allow", self.hook_decision(proc))

    def test_the_harnesss_own_search_keeps_its_own_prompts(self):
        proc = self.call("WebSearch", {"query": "uv run frozen flag"})
        self.assertNotIn(self.hook_decision(proc), ("allow", "deny"))
