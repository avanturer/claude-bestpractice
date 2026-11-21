"""A stack of pull requests is landed one step a call, in the order GitHub keeps it open (#366).

A project landed its stacks with a command of its own: retarget, rebase, `--force-with-lease`,
`gh pr merge` in a loop and `git worktree remove --force`. The merge gate refuses the loop, and
the forced removal skipped what the plugin keeps a tree for. `claude-bp-ship --stack` names the
next command and runs none; the merge gate refuses a deletion that would close a stack's pull
request, which GitHub closes and never reopens once its base branch goes (#293, #329).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from helpers import BIN, RepoCase, git

from claude_bestpractice import pullrequest

GH = """#!/usr/bin/env python3
import json, sys
state = json.load(open(%r))
args = sys.argv[1:]
if args[:2] == ["pr", "list"]:
    print(json.dumps([p for p in state["pulls"] if p.get("open", True)]))
elif args[:2] == ["pr", "view"]:
    print(state.get("states", {}).get(args[2], "OPEN"))
elif args[:2] == ["repo", "view"]:
    print(json.dumps({"squashMergeAllowed": True}))
else:
    sys.exit(1)
"""


class StackCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.origin = self.tmp / "origin.git"
        git(["init", "-q", "--bare", str(self.origin)], self.tmp)
        git(["remote", "add", "origin", str(self.origin)], self.repo)
        git(["push", "-q", "origin", "main"], self.repo)
        git(["switch", "-qc", "feat/a"], self.repo)
        self.write("a.txt", "a\n")
        self.a = self.commit("a")
        git(["switch", "-qc", "feat/b"], self.repo)
        self.write("b.txt", "b\n")
        self.b = self.commit("b")
        git(["switch", "-q", "main"], self.repo)
        git(["push", "-q", "origin", "feat/a", "feat/b"], self.repo)
        self.tree_a = self.tmp / "tree-a"
        self.tree_b = self.tmp / "tree-b"
        git(["worktree", "add", "-q", str(self.tree_a), "feat/a"], self.repo)
        git(["worktree", "add", "-q", str(self.tree_b), "feat/b"], self.repo)
        self.state_file = self.tmp / "gh-state.json"
        self.github({"pulls": [
            {"number": 1, "headRefName": "feat/a", "baseRefName": "main", "headRefOid": self.a},
            {"number": 2, "headRefName": "feat/b", "baseRefName": "feat/a", "headRefOid": self.b},
        ]})
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        (stubs / "gh").write_text(GH % str(self.state_file))
        (stubs / "gh").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}"}

    def github(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state))

    def edit(self, change) -> None:
        state = json.loads(self.state_file.read_text())
        change(state)
        self.github(state)

    def step(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "claude-bp-ship"), "--stack"],
                              capture_output=True, text=True, cwd=str(self.repo), env=self.env,
                              timeout=120)

    def run_as_printed(self, command: str) -> int:
        """Run a printed step as a session would, each `&&` part in turn, with no shell."""
        for part in command.split(" && "):
            proc = subprocess.run(shlex.split(part), cwd=str(self.repo), capture_output=True,
                                  timeout=120)
            if proc.returncode:
                return proc.returncode
        return 0

    def next_command(self) -> str:
        proc = self.step()
        lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("next:")]
        return lines[0].split("next:", 1)[1].strip() if lines else ""


class TestTheStackIsLandedInOrder(StackCase):
    def test_the_next_pull_request_moves_onto_the_trunk_first(self):
        self.assertEqual("gh pr edit 2 --base main", self.next_command())

    def test_then_the_lowest_merges_from_its_own_tree_deleting_its_branch(self):
        self.next_command()
        self.edit(lambda s: s["pulls"][1].update(baseRefName="main"))
        command = self.next_command()
        self.assertEqual(f"cd {self.tree_a.resolve()} && gh pr merge 1 --squash --delete-branch",
                         command)
        # One merge, as a command of its own: what the merge gate takes (decision 0040).
        self.assertEqual("", pullrequest.merge_line("Bash", command))

    def test_then_the_one_above_is_rebased_off_its_commits_pushed_and_the_tree_goes(self):
        self.next_command()
        self.edit(lambda s: s["pulls"][1].update(baseRefName="main"))
        self.next_command()
        # GitHub squashed #1 onto main and deleted feat/a.
        git(["merge", "-q", "--squash", "feat/a"], self.repo)
        git(["commit", "-qm", "a (#1)"], self.repo)
        git(["push", "-q", "origin", "main"], self.repo)
        self.edit(lambda s: (s["pulls"][0].update(open=False), s.setdefault("states", {}).update({"1": "MERGED"})))

        rebase = self.next_command()
        self.assertIn(f"rebase --onto origin/main {self.a} feat/b", rebase)
        self.assertEqual(0, self.run_as_printed(rebase))
        push = self.next_command()
        self.assertIn(f"push --force-with-lease=feat/b:{self.b} origin feat/b", push)
        # Where the checks run on the rebased commit is said with the push (no hook here).
        self.assertIn("No pre-push hook runs the project's checks here", self.step().stdout)
        self.assertEqual(0, self.run_as_printed(push))
        rebased = git(["rev-parse", "feat/b"], self.repo)
        self.edit(lambda s: s["pulls"][1].update(headRefOid=rebased))

        remove = self.next_command()
        self.assertEqual(f"git -C {self.repo.resolve()} worktree remove {self.tree_a.resolve()}",
                         remove)
        self.assertNotIn("--force", remove)
        git(["worktree", "remove", str(self.tree_a)], self.repo)
        proc = self.step()
        self.assertIn("no stack is open here", proc.stdout)
        self.assertEqual(0, proc.returncode)
        # What landed is only #1's squash below #2's own commit.
        self.assertEqual(["b", "a (#1)"], git(["log", "--format=%s", "-2", "feat/b"],
                                              self.repo).splitlines())


class TestWhatStopsTheStack(StackCase):
    def test_a_draft_at_the_bottom_is_not_merged(self):
        self.edit(lambda s: s["pulls"][0].update(isDraft=True))
        self.assertEqual("gh pr edit 2 --base main", self.next_command())
        self.edit(lambda s: s["pulls"][1].update(baseRefName="main"))
        proc = self.step()
        self.assertEqual(1, proc.returncode)
        self.assertIn("is a draft", proc.stdout)

    def test_a_bottom_closed_unmerged_rebases_nothing(self):
        self.next_command()
        self.edit(lambda s: (s["pulls"][0].update(open=False), s.setdefault("states", {}).update({"1": "CLOSED"})))
        proc = self.step()
        self.assertEqual(1, proc.returncode)
        self.assertIn("closed, not merged", proc.stdout)

    def test_no_stack_is_said_so(self):
        self.github({"pulls": [{"number": 1, "headRefName": "feat/a", "baseRefName": "main",
                                "headRefOid": self.a}]})
        proc = self.step()
        self.assertEqual(0, proc.returncode)
        self.assertIn("no stack is open here", proc.stdout)

    def test_no_gh_answer_is_no_step(self):
        self.state_file.write_text("not json")
        proc = self.step()
        self.assertEqual(1, proc.returncode)
        self.assertIn("could not list", proc.stdout)


class GateCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/a", "main", "s1", number=1)
        pullrequest.opened(ctx, "feat/b", "main", "s1", number=2, into="feat/a")

    def bash(self, command: str):
        return self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                          "tool_name": "Bash", "tool_input": {"command": command}})


class TestADeletionThatClosesAStacksPullRequestIsRefused(GateCase):
    def test_a_merge_that_deletes_the_branch_below_is_refused_naming_the_move(self):
        proc = self.bash("gh pr merge 1 --squash --delete-branch")
        self.assertEqual("deny", self.hook_decision(proc))
        why = self.hook_reason(proc)
        self.assertIn("gh pr edit 2 --base main", why)
        self.assertIn("claude-bp-ship --stack", why)

    def test_the_short_flag_is_the_same_deletion(self):
        self.assertIn("gh pr edit 2 --base main", self.hook_reason(self.bash("gh pr merge 1 -s -d")))

    def test_a_merge_that_keeps_the_branch_is_not_refused_for_it(self):
        self.assertNotIn("gh pr edit 2", self.hook_reason(self.bash("gh pr merge 1 --squash")))

    def test_deleting_the_trunk_is_left_to_the_trunk_gate(self):
        """`git push origin :main` is refused for writing the trunk, never told to move onto it."""
        self.assertNotIn("Move it onto", self.hook_reason(self.bash("git push origin :main")))

    def test_a_push_that_deletes_the_branch_below_is_refused(self):
        for line in ("git push origin --delete feat/a", "git push origin :feat/a"):
            proc = self.bash(line)
            self.assertEqual("deny", self.hook_decision(proc), line)
            self.assertIn("#2", self.hook_reason(proc), line)

    def test_once_moved_the_merge_is_not_refused_for_it(self):
        moved = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                 "tool_input": {"command": "gh pr edit 2 --base main"},
                 "tool_response": {"stdout": "https://github.com/o/r/pull/2"}}
        self.run_hook("pr-opened", moved)
        self.assertEqual([], pullrequest.stacked_on(self.ctx(), "feat/a"))
        self.assertNotIn("gh pr edit 2", self.hook_reason(
            self.bash("gh pr merge 1 --squash --delete-branch")))


class TestATypedRemovalKeepsWhatNothingElseHas(GateCase):
    """The landed tree is removed the way the plugin removes its own (decision 0039)."""

    def setUp(self) -> None:
        super().setUp()
        self.write(".gitignore", "exports/\nnode_modules/\n")
        self.commit("ignore")
        self.tree = self.add_worktree("feat-x")

    def test_an_ignored_file_nothing_else_has_keeps_the_tree(self):
        (self.tree / "exports").mkdir()
        (self.tree / "exports" / "photo.png").write_text("only here")
        proc = self.bash(f"git -C {self.repo} worktree remove {self.tree}")
        self.assertEqual("deny", self.hook_decision(proc))
        # git names a directory nothing in it is tracked in whole, as `exports/`.
        self.assertIn("exports/", self.hook_reason(proc))
        self.assertTrue(self.tree.is_dir())

    def test_what_regrows_does_not(self):
        (self.tree / "node_modules" / "x").mkdir(parents=True)
        (self.tree / "node_modules" / "x" / "index.js").write_text("cache")
        self.assertNotIn("nothing else holds", self.hook_reason(
            self.bash(f"git -C {self.repo} worktree remove {self.tree}")))


class TestARetargetIsReadAsGhTakesIt(GateCase):
    def test_the_shapes(self):
        for line, expected in (
            ("gh pr edit 2 --base main", (2, "main")),
            ("gh pr edit --base main 2", (2, "main")),
            ("gh pr edit --title 'x 5' -B main https://github.com/o/r/pull/7", (7, "main")),
            ("gh pr edit --base=release", (0, "release")),
        ):
            self.assertEqual(expected, pullrequest.retargeted("Bash", line, {}), line)
        self.assertIsNone(pullrequest.retargeted("Bash", "gh pr edit 2 --title x", {}))
        self.assertEqual((4, "main"), pullrequest.retargeted(
            "mcp__github__update_pull_request", "", {"pullNumber": 4, "base": "main"}))
