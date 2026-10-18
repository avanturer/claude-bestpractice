"""A call is judged by the repository it acts on. Read off the shell alone, a session left
standing in `/home/user` was outside every rule: `rm` of the red-suite record went through, and a
merge with the GitHub tool was judged by nothing, which is how 2.4.1's own release merged."""

from __future__ import annotations

import json
import os

from helpers import RepoCase

from claude_bestpractice import evidence, store

MERGE = {"owner": "o", "repo": "r", "pullNumber": 7}


class TestACallIsJudgedWhereItActs(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.outside = self.tmp / "elsewhere"
        self.outside.mkdir()
        self.env = {key: value for key, value in os.environ.items() if key != "CLAUDE_PROJECT_DIR"}

    def from_outside(self, tool: str, tool_input: dict, **event):
        return self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                          "tool_name": tool, "tool_input": tool_input,
                                          "cwd": str(self.outside), **event},
                             env=self.env, cwd=self.outside)

    def test_the_red_record_cannot_be_deleted_from_outside(self):
        red = store.tier_a(self.ctx(), evidence.RED_SUITE_FILE)
        store.write_json(red, {"command": ["make", "test"], "tail": "FAILED", "executed": 1})
        proc = self.from_outside("Bash", {"command": f"rm {red}"})
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)
        self.assertTrue(red.exists())

    def test_nor_the_config_written_with_a_tool(self):
        config = self.repo / ".claude" / "claude-bestpractice" / "config.json"
        proc = self.from_outside("Write", {"file_path": str(config), "content": "{}"})
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)

    def test_a_merge_with_the_tool_is_judged_in_the_project_the_session_began_in(self):
        self.env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        proc = self.from_outside("mcp__github__merge_pull_request", MERGE)
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)
        self.assertIn("+merge", self.hook_reason(proc))

    def test_or_in_the_last_place_the_session_worked(self):
        transcript = self.tmp / "transcript.jsonl"
        compact = {"separators": (",", ":")}
        transcript.write_text(json.dumps({"type": "user", "cwd": str(self.repo)}, **compact) + "\n"
                              + json.dumps({"type": "user", "cwd": str(self.outside)}, **compact)
                              + "\n", encoding="utf-8")
        proc = self.from_outside("mcp__github__merge_pull_request", MERGE,
                                 transcript_path=str(transcript))
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)

    def test_a_call_that_touches_no_repository_is_none_of_its_business(self):
        note = self.outside / "note.txt"
        proc = self.from_outside("Bash", {"command": f"echo hi > {note}"})
        self.assertNotEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)
        proc = self.from_outside("mcp__github__merge_pull_request", MERGE)
        self.assertNotEqual("deny", self.hook_decision(proc), "a merge of nothing this plugin serves")
