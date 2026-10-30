"""A tool an MCP server offers that writes files is judged as the write it is (decision 0060).

Serena and the reference filesystem server write files with tools of their own, and every rule
that reads a write's path saw nothing of them.
"""

from __future__ import annotations

import json

from helpers import REPO_ROOT, RepoCase, harness_matches

from claude_bestpractice import servers

CREATES = "mcp__serena__create_text_file"
WRITES = "mcp__filesystem__write_file"
MOVES = "mcp__filesystem__move_file"


class TestWhatIsAWrite(RepoCase):
    def test_a_servers_file_tool_is_the_write_of_the_file_it_names(self):
        for tool, given, path in ((CREATES, {"relative_path": "src/a.py", "content": "x"}, "src/a.py"),
                                  (WRITES, {"path": "/w/src/a.py", "content": "x"}, "/w/src/a.py"),
                                  ("mcp__serena__replace_symbol_body",
                                   {"name_path": "A/f", "relative_path": "src/a.py", "body": "x"},
                                   "src/a.py")):
            with self.subTest(tool=tool):
                read = servers.as_a_write({"tool_name": tool, "tool_input": given})
                self.assertEqual("Write", read["tool_name"])
                self.assertEqual(path, read["tool_input"]["file_path"])
                self.assertEqual("x", read["tool_input"]["content"])
                self.assertEqual(tool, read[servers.SERVED_BY])

    def test_a_move_is_the_mv_it_is(self):
        read = servers.as_a_write({"tool_name": MOVES, "tool_input": {
            "source": "src/a b.py", "destination": "src/c.py"}})
        self.assertEqual({"command": "mv -- 'src/a b.py' src/c.py"}, read["tool_input"])
        self.assertEqual("Bash", read["tool_name"])

    def test_reading_or_naming_no_file_is_no_write(self):
        for tool, given in (("mcp__serena__read_file", {"relative_path": "src/a.py"}),
                            ("mcp__serena__find_symbol", {"name_path": "A"}),
                            (CREATES, {"content": "x"}), (MOVES, {"source": "a"}),
                            ("Write", {"file_path": "a"})):
            with self.subTest(tool=tool):
                self.assertIsNone(servers.as_a_write({"tool_name": tool, "tool_input": given}))

    def test_the_hook_is_wired_for_them(self):
        hooks = json.loads((REPO_ROOT / "plugin" / "hooks" / "hooks.json").read_text())
        matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
        for tool in (CREATES, WRITES, MOVES, "mcp__serena__replace_content"):
            self.assertTrue(harness_matches(matcher, tool), tool)
        self.assertFalse(harness_matches(matcher, "mcp__serena__read_file"))


class TestTheGateJudgesThem(RepoCase):
    def call(self, tool: str, given: dict):
        return self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                          "tool_name": tool, "tool_input": given})

    def test_a_servers_write_to_the_founders_switches_is_refused(self):
        proc = self.call(CREATES, {"relative_path": ".claude/claude-bestpractice/config.json",
                                   "content": "{\"enabled\": false}"})
        self.assertEqual("deny", self.hook_decision(proc))

    def test_one_the_gate_lets_through_keeps_its_servers_prompt(self):
        self.claim_a_task("s1", "src/a.py")
        proc = self.call(CREATES, {"relative_path": "src/a.py", "content": "x = 1\n"})
        self.assertNotIn(self.hook_decision(proc), ("allow", "deny"), self.hook_reason(proc))
