"""An agent that only searches the web is counted apart from the ones that search the tree (#378).

Six researchers of one turn, each on its own subject and with nothing but the web to read, were
refused three of six as agents "searching the same tree": `subagent_fanout` is the ceiling on
copies of one answer about one repository, and these read no repository. An agent whose own
definition lists no tool but the web's is counted against `research_fanout` instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from helpers import BIN, REPO_ROOT
from test_subagents import SpawnCase

from claude_bestpractice import subagents

WEB_ONLY = ("---\nname: researcher\ndescription: searches\nmodel: sonnet\n"
            "tools: WebSearch, WebFetch, mcp__plugin_bestpractice_parallel__web_search\n---\n\nGo.\n")


class ResearchCase(SpawnCase):
    def research(self, n: int, first: int = 0, kind: str = "researcher") -> list[tuple[str, str]]:
        return [self.decision(self.gate({"subagent_type": kind, "prompt": f"subject {i}"}))
                for i in range(first, first + n)]

    def explore(self, n: int, first: int = 0) -> list[tuple[str, str]]:
        return [self.decision(self.gate(
            {"subagent_type": "Explore", "model": "haiku", "prompt": f"find {i}"}))
            for i in range(first, first + n)]


class TestAWebOnlyAgentIsCountedApart(ResearchCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(".claude/agents/researcher.md", WEB_ONLY)

    def test_six_researchers_of_one_turn_all_start(self):
        """The #378 turn: six subjects, six agents with nothing but the web."""
        self.start()
        self.assertNotIn("deny", [d for d, _ in self.research(6)])

    def test_they_leave_the_tree_ceiling_whole(self):
        self.start()
        self.research(6)
        self.assertNotIn("deny", [d for d, _ in self.explore(3)])
        decision, why = self.decision(self.gate(
            {"subagent_type": "Explore", "model": "haiku", "prompt": "a fourth search"}))
        self.assertEqual("deny", decision)
        self.assertIn("ceiling is 3", why)

    def test_past_research_fanout_they_are_refused_with_their_own_ceiling(self):
        self.start()
        self.assertNotIn("deny", [d for d, _ in self.research(8)])
        decision, why = self.decision(self.gate({"subagent_type": "researcher", "prompt": "a ninth"}))
        self.assertEqual("deny", decision)
        self.assertIn("research_fanout", why)
        self.assertIn("ceiling for them is 8", why)

    def test_the_founders_research_ceiling_is_read(self):
        self.configure(research_fanout=2)
        self.start()
        self.research(2)
        decision, why = self.decision(self.gate({"subagent_type": "researcher", "prompt": "a third"}))
        self.assertEqual("deny", decision)
        self.assertIn("ceiling for them is 2", why)

    def test_zero_switches_the_research_ceiling_off(self):
        self.configure(research_fanout=0)
        self.start()
        self.assertNotIn("deny", [d for d, _ in self.research(12)])

    def test_the_research_count_starts_again_on_the_founders_next_message(self):
        self.configure(research_fanout=2)
        self.start()
        self.research(2)
        subprocess.run(
            [sys.executable, str(BIN / "prompt-capture")],
            input=json.dumps({"cwd": str(self.repo), "session_id": "s1",
                              "hook_event_name": "UserPromptSubmit", "prompt": "now the next part"}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120)
        self.assertEqual(0, self.record().research_spawns_this_turn)
        self.assertNotIn("deny", [d for d, _ in self.research(2, first=10)])

    def test_the_tree_refusal_names_the_way_apart(self):
        self.start()
        self.explore(3)
        _, why = self.decision(self.gate(
            {"subagent_type": "Explore", "model": "haiku", "prompt": "one more search"}))
        self.assertIn("research_fanout", why)
        self.assertIn(subagents.WEB_RESEARCHER, why)


class TestOnlyAnAgentThatCannotReadTheTreeIsCountedApart(ResearchCase):
    def counted_as_research(self, definition: str | None, kind: str = "helper") -> bool:
        if definition is not None:
            self.write(f".claude/agents/{kind}.md", definition)
        return subagents.reads_no_tree(self.ctx(), kind)

    def test_a_definition_that_names_no_tools_has_them_all(self):
        self.assertFalse(self.counted_as_research("---\nname: helper\nmodel: haiku\n---\nHelp.\n"))

    def test_one_tool_that_reads_files_counts_it_with_the_tree(self):
        self.assertFalse(self.counted_as_research(
            "---\nname: helper\nmodel: haiku\ntools: WebSearch, Read\n---\nHelp.\n"))
        self.assertFalse(self.counted_as_research(
            "---\nname: helper\nmodel: haiku\ntools: WebFetch, Bash\n---\nHelp.\n"))

    def test_a_built_in_agent_has_no_definition_to_say_so(self):
        for kind in ("general-purpose", "Explore", "Plan", "fork"):
            self.assertFalse(subagents.reads_no_tree(self.ctx(), kind), kind)

    def test_the_tools_listed_on_lines_of_their_own_are_read(self):
        self.assertTrue(self.counted_as_research(
            "---\nname: helper\nmodel: haiku\ntools:\n  - WebSearch\n  - \"WebFetch\"\n---\nHelp.\n"))
        self.assertFalse(self.counted_as_research(
            "---\nname: helper\nmodel: haiku\ntools:\n  - WebSearch\n  - Grep\n---\nHelp.\n"))

    def test_a_bracketed_list_is_read(self):
        self.assertTrue(self.counted_as_research(
            "---\nname: helper\nmodel: haiku\ntools: [WebSearch, mcp__exa__web_search_exa]\n---\nH.\n"))

    def test_a_tools_line_in_the_body_is_not_the_definitions(self):
        self.assertFalse(self.counted_as_research(
            "---\nname: helper\nmodel: haiku\n---\ntools: WebSearch\n"))

    def test_the_researcher_this_plugin_ships_reads_no_tree(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": str(REPO_ROOT / "plugin")}):
            self.assertTrue(subagents.reads_no_tree(self.ctx(), subagents.WEB_RESEARCHER))
            self.assertEqual("sonnet", subagents.pinned_tier(self.ctx(), subagents.WEB_RESEARCHER))


class TestTheShippedResearcherIsWhatTheRefusalNames(ResearchCase):
    def test_its_file_is_where_the_name_points(self):
        name = subagents.WEB_RESEARCHER.split(":")[-1]
        self.assertTrue((REPO_ROOT / "plugin" / "agents" / f"{name}.md").is_file())
