"""A call is judged by the repository it acts on. Read off the shell alone, a session left
standing in `/home/user` was outside every rule: `rm` of the red-suite record went through, and a
merge with the GitHub tool was judged by nothing, which is how 2.4.1's own release merged."""

from __future__ import annotations

import json
import os
import shutil

from helpers import RepoCase
from test_pullrequest import PRCase

from claude_bestpractice import evidence, pullrequest, store

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

    def test_a_write_into_a_directory_not_made_yet_is_judged(self):
        """`Write` makes the directories it needs. Taken as it was, a parent that did not exist
        yet named no repository, and the write went through unjudged."""
        state = self.repo / ".claude" / "claude-bestpractice"
        shutil.rmtree(state, ignore_errors=True)
        proc = self.from_outside("Write", {"file_path": str(state / "config.json"), "content": "{}"})
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)

    def test_a_project_whose_tree_is_gone_is_judged_by_the_clone_that_held_it(self):
        """A session that removed its worktree stands nowhere, and the tree its project or its
        transcript names held no repository any more: the merge went through unjudged."""
        self.env["CLAUDE_PROJECT_DIR"] = str(self.repo / ".claude" / "worktrees" / "gone")
        proc = self.from_outside("mcp__github__merge_pull_request", MERGE)
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)

    def test_a_shell_standing_inside_git_is_placed_in_its_tree(self):
        """Inside `.git` there is no working tree to ask git for, so the call went unjudged."""
        red = store.tier_a(self.ctx(), evidence.RED_SUITE_FILE)
        store.write_json(red, {"command": ["make", "test"], "tail": "FAILED", "executed": 1})
        inside = self.repo / ".git"
        proc = self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                          "tool_name": "Bash", "tool_input": {"command": f"rm {red}"},
                                          "cwd": str(inside)}, env=self.env, cwd=inside)
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)

    def test_a_call_that_touches_no_repository_is_none_of_its_business(self):
        note = self.outside / "note.txt"
        proc = self.from_outside("Bash", {"command": f"echo hi > {note}"})
        self.assertNotEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)
        proc = self.from_outside("mcp__github__merge_pull_request", MERGE)
        self.assertNotEqual("deny", self.hook_decision(proc), "a merge of nothing this plugin serves")


class TestOutsideTheSessionIsTheSessionOfItsTree(PRCase):
    """From outside every repository the session was the bare harness id, which holds none of its
    records, and only pre-tool looked past the shell: the founder's `+merge` typed there was
    dropped, a merge from there was refused for want of the word said in the tree, and one let
    through was never settled, its pull request left open on the board and its card in `doing`."""

    def setUp(self) -> None:
        super().setUp()
        self.outside = self.tmp / "elsewhere"
        self.outside.mkdir()
        self.env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.repo)}
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()

    def from_outside(self, name: str, event: dict):
        return self.run_hook(name, {"session_id": "s1", "cwd": str(self.outside), **event},
                             env=self.env, cwd=self.outside)

    def merge_from_outside(self):
        """The request, and the response where it was allowed, as the harness sends them."""
        call = {"tool_name": "mcp__github__merge_pull_request", "tool_use_id": "toolu_outside",
                "tool_input": {"owner": "o", "repo": "r", "pullNumber": PRCase.PR_NUMBER}}
        proc = self.from_outside("pre-tool", {**call, "hook_event_name": "PreToolUse"})
        if self.decision(proc) != "deny":
            self.from_outside("pr-opened", {**call, "hook_event_name": "PostToolUse",
                                            "tool_response": {"stdout": "", "stderr": ""}})
        return proc

    def test_the_word_said_in_the_tree_covers_a_merge_made_from_outside(self):
        self.accept()
        proc = self.merge_from_outside()
        self.assertNotEqual("deny", self.decision(proc), proc.stdout + proc.stderr)
        self.assertEqual("", pullrequest.line(self.ctx()), "settled where it was judged")

    def test_the_word_said_while_the_shell_stands_outside_is_the_sessions(self):
        self.from_outside("prompt-capture", {"hook_event_name": "UserPromptSubmit",
                                             "prompt": "looks good to me\n+merge"})
        proc = self.merge_from_outside()
        self.assertNotEqual("deny", self.decision(proc), proc.stdout + proc.stderr)
        self.assertEqual("", pullrequest.line(self.ctx()))
