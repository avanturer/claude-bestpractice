"""Every tree of a clone shares one stash list (decision 0057).

Checked live: `git stash pop` in one session's tree took the unfinished work another session's
tree had stashed a minute before. A `pop`, `drop`, `clear` or `branch` of an entry stashed on
another branch is refused; `apply` copies it and leaves it to whoever left it.
"""

from __future__ import annotations

from helpers import RepoCase, git

from claude_bestpractice import gitpolicy


class StashCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.mine = self.add_worktree("feat-mine")
        self.theirs = self.add_worktree("feat-theirs")

    def stash_in(self, tree, text: str) -> None:
        (tree / "README.md").write_text(text)
        git(["stash"], tree)

    def pre_tool(self, command: str, tree):
        return self.run_hook("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}}, cwd=tree)


class TestAnotherTreesEntryIsNotTaken(StashCase):
    def test_popping_the_top_entry_another_branch_stashed_is_refused(self):
        self.stash_in(self.theirs, "their unfinished work\n")
        proc = self.pre_tool("git stash pop", self.mine)

        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn("stash@{0}, which was stashed on feat-theirs", self.hook_reason(proc))
        self.assertIn("git stash apply stash@{0}", self.hook_reason(proc))

    def test_every_verb_that_takes_one_off_the_list_is_judged(self):
        self.stash_in(self.theirs, "their unfinished work\n")
        for args in (["pop"], ["drop"], ["drop", "stash@{0}"], ["pop", "--index", "0"], ["clear"],
                     ["branch", "rescue"], ["branch", "rescue", "stash@{0}"]):
            with self.subTest(args=args):
                self.assertEqual(("stash@{0}", "feat-theirs"),
                                 gitpolicy.foreign_stash(self.mine, args))

    def test_its_own_entry_is_its_own_to_take(self):
        self.stash_in(self.theirs, "their unfinished work\n")
        self.stash_in(self.mine, "my unfinished work\n")

        self.assertIsNone(gitpolicy.foreign_stash(self.mine, ["pop"]))
        self.assertEqual(("stash@{1}", "feat-theirs"),
                         gitpolicy.foreign_stash(self.mine, ["drop", "stash@{1}"]))
        self.assertEqual(("stash@{1}", "feat-theirs"), gitpolicy.foreign_stash(self.mine, ["clear"]))

    def test_copying_one_or_making_one_takes_nothing(self):
        self.stash_in(self.theirs, "their unfinished work\n")
        for args in (["apply"], ["list"], ["show", "-p"], ["push", "-m", "x"], []):
            with self.subTest(args=args):
                self.assertIsNone(gitpolicy.foreign_stash(self.mine, args))
        self.assertNotEqual("deny", self.hook_decision(self.pre_tool("git stash apply", self.mine)))

    def test_the_tree_that_stashed_it_pops_it(self):
        self.stash_in(self.theirs, "their unfinished work\n")
        self.assertNotEqual("deny", self.hook_decision(self.pre_tool("git stash pop", self.theirs)))

    def test_an_empty_list_takes_nothing(self):
        self.assertIsNone(gitpolicy.foreign_stash(self.mine, ["pop"]))
