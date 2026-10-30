"""A session starting in the main checkout starts on the trunk (decision 0061).

Sessions work in their own trees and merge on GitHub, so nothing moved the main checkout: a
founder's stood 107 commits behind `origin/main` while every session started in it read, and
was told to fix, code the trunk had replaced.
"""

from __future__ import annotations

import json

from helpers import RepoCase, git

from claude_bestpractice import sessions


class TestTheMainCheckoutIsBroughtUp(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("src/a.py", "x = 1\n")
        self.commit("one")
        self.write("src/b.py", "y = 1\n")
        self.commit("two")
        git(["update-ref", "refs/remotes/origin/main", "HEAD"], self.repo)
        git(["reset", "-q", "--hard", "HEAD~2"], self.repo)
        self.trunk = git(["rev-parse", "origin/main"], self.repo).strip()

    def start(self, session_id: str = "s1", cwd=None) -> str:
        proc = self.run_hook("session-start", {"session_id": session_id,
                                               "hook_event_name": "SessionStart"}, cwd=cwd)
        return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]

    def head(self) -> str:
        return git(["rev-parse", "HEAD"], self.repo).strip()

    def test_a_session_starting_there_brings_it_level_and_starts_from_there(self):
        said = self.start()

        self.assertIn("2 commit(s) behind origin/main, and now is level with it", said)
        self.assertEqual(self.trunk, self.head())
        records = sessions.load_all(self.ctx())
        self.assertEqual([self.trunk], [record.baseline_commit for record in records])

    def test_a_file_the_trunk_would_overwrite_is_kept_and_named(self):
        self.write("src/a.py", "mine\n")
        said = self.start()

        self.assertIn("was refused", said)
        self.assertNotEqual(self.trunk, self.head())
        self.assertEqual("mine\n", (self.repo / "src" / "a.py").read_text())

    def test_another_live_session_in_it_leaves_it_where_it_is(self):
        sessions.register(self.ctx(), self.session_record("someone-else"))
        self.assertNotIn("level with it", self.start())
        self.assertNotEqual(self.trunk, self.head())

    def test_a_session_starting_in_its_own_tree_leaves_it_alone(self):
        tree = self.add_worktree("feat-x")
        self.start(cwd=tree)
        self.assertNotEqual(self.trunk, self.head())

    def test_commits_of_its_own_are_named_and_not_moved(self):
        self.write("src/c.py", "z = 1\n")
        mine = self.commit("only here")

        self.assertIn("has 1 commit(s) origin/main does not, and is 2 behind it", self.start())
        self.assertEqual(mine.strip(), self.head())

    def test_level_already_says_nothing(self):
        git(["reset", "-q", "--hard", "origin/main"], self.repo)
        self.assertNotIn("main checkout", self.start())
