"""Issue #224: the tree went and the cleanup after it could not.

#218 let a session remove its own worktree and #220 made it remove it unasked. What neither
covered is the second the directory disappears: git obliges, the shell is left with no
working directory at all, and Claude Code refuses every later git call as "isolated in the
worktree <gone>". So the rest of the tidying-up — the branch, the database this plugin gave
the tree, the small branches the same session merged and left — had nobody who could do it,
and the founder's repository kept three merged branches and a sixteen-megabyte database per
finished task.

Two halves, both here: the removal carries everything it created, and a removal asked for by
hand is performed from the main checkout rather than refused or survived.
"""

from __future__ import annotations

import unittest

from helpers import git
from test_closing_the_work import TreeCase

from claude_bestpractice import worktree


class CleanupCase(TreeCase):
    """#220's fixture, which is the state this starts from: one tree this plugin made for
    one session, over a trunk ref this clone actually has."""

    def a_side_branch(self, name: str, relpath: str) -> str:
        """A branch made beside the work and merged, the way a session makes three a day."""
        self.a_branch_with_work(name, relpath)
        self.merge(name)
        return name

    def a_branch_with_work(self, name: str, relpath: str) -> None:
        """A branch with a commit on it and no tree standing on it — every branch a session
        opens for a small change and closes on GitHub."""
        git(["branch", name, "origin/main"], self.repo)
        side = self.tmp / f"side-{name.replace('/', '-')}"
        git(["worktree", "add", "-q", str(side), name], self.repo)
        (side / relpath).parent.mkdir(parents=True, exist_ok=True)
        (side / relpath).write_text("side\n", encoding="utf-8")
        git(["add", "-A"], side)
        git(["commit", "-qm", f"the {name} work"], side)
        git(["worktree", "remove", str(side)], self.repo)

    def a_finished_tree(self) -> tuple:
        """The state every test here begins in: merged work, a closed card, a tree to go."""
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        return tree, branch

    def branches(self) -> str:
        return git(["branch", "--format=%(refname:short)"], self.repo)


class TestTheRemovalCarriesTheRest(CleanupCase):
    def test_merged_branches_of_this_session_go_with_the_tree(self):
        """`fix/…` and `docs/…`, merged on GitHub and left here — two of the three kinds of
        rubbish #224 counted, and neither belongs to any tree, so no tree-shaped sweep could
        ever have reached them."""
        tree, branch = self.a_finished_tree()
        self.a_side_branch("fix/correct-sheet", "src/sheet.py")
        self.a_side_branch("docs/close-semantics", "src/doc.py")

        gone = worktree.release_mine(self.ctx(), self.session)

        self.assertIsNotNone(gone, "the finished tree was not released")
        self.assertNotIn("fix/correct-sheet", self.branches())
        self.assertNotIn("docs/close-semantics", self.branches())

    def test_a_squash_merged_side_branch_goes_too(self):
        """The case `git branch -d` cannot see, and the one every merge in this repository
        produces: the tip is an ancestor of nothing and every file it delivered is in."""
        tree, branch = self.a_finished_tree()
        self.a_branch_with_work("fix/squashed", "src/squashed.py")
        self.merge("fix/squashed", squash=True)

        worktree.release_mine(self.ctx(), self.session)

        self.assertNotIn("fix/squashed", self.branches())

    def test_an_unmerged_branch_is_left_exactly_where_it_is(self):
        """The whole safety argument, unchanged: work nobody merged is work somebody did."""
        tree, branch = self.a_finished_tree()
        self.a_branch_with_work("feat/still-going", "src/unmerged.py")

        worktree.release_mine(self.ctx(), self.session)

        self.assertIn("feat/still-going", self.branches())

    def test_a_branch_another_tree_is_standing_on_is_never_taken(self):
        tree, branch = self.a_finished_tree()
        git(["branch", "feat/occupied", "origin/main"], self.repo)
        git(["worktree", "add", "-q", str(self.tmp / "occupied"), "feat/occupied"], self.repo)

        worktree.release_mine(self.ctx(), self.session)

        self.assertIn("feat/occupied", self.branches())

    def test_the_trunk_survives_its_own_sweep(self):
        tree, branch = self.a_finished_tree()

        worktree.release_mine(self.ctx(), self.session)

        self.assertIn("main", self.branches())

    def test_what_went_is_said_rather_than_done_silently(self):
        tree, branch = self.a_finished_tree()
        self.a_side_branch("fix/named-in-the-line", "src/named.py")

        _path, _branch, also = worktree.release_mine(self.ctx(), self.session)

        self.assertTrue(any("fix/named-in-the-line" in line for line in also), also)


class TestTheDatabaseIsOnlyEverOurs(CleanupCase):
    """No server is needed to prove the guards, and the guards are the whole of it: this
    plugin drops a database it derived the name of, for a tree it made, that nothing else
    points at. Everything else is somebody's data."""

    def test_a_name_that_is_not_the_one_we_derived_is_never_dropped(self):
        dropped = worktree.drop_database(
            "postgresql://localhost:5432/production", "repo_feat_the_work")
        self.assertEqual("", dropped)

    def test_a_database_this_plugin_does_not_speak_is_never_dropped(self):
        dropped = worktree.drop_database("mysql://localhost:3306/app_dev", "app_dev")
        self.assertEqual("", dropped)

    def test_a_tree_that_shares_a_database_keeps_it(self):
        """`isolate_databases: false`, and the main checkout's own database: the tree goes
        and what it was pointing at was never its own."""
        tree, _branch = self.a_tree()
        url = "postgresql://localhost:5432/shared_dev"
        (self.repo / ".env").write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
        (tree / ".env").write_text(f"DATABASE_URL={url}\n", encoding="utf-8")

        self.assertTrue(worktree._points_at(self.ctx(), url, tree))


class TestARemovalAskedForByHand(CleanupCase):
    """The command that strands the session, and what the gate does with it instead."""

    def a_live_session(self, tree) -> None:
        from claude_bestpractice import sessions

        record = self.session_record("s1")
        record.worktree = tree.as_posix()
        sessions.register(self.ctx(), record)

    def removing(self, tree, extra: str = ""):
        """From the main checkout, where a session can still stand with its work in its tree.
        Claude Code reports the tree itself as the hook's working directory once the session
        has `cd`-ed into it (2.1.280, 2.1.281); the same removal asked from inside the tree is
        driven in `test_one_session_two_trees`."""
        return self.run_hook("pre-tool", {
            "session_id": "s1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git worktree remove {extra}{tree}"},
        }, cwd=self.repo)

    def test_the_tree_is_removed_by_the_gate_and_the_call_is_not_run(self):
        """Not refused — done. A gate that stands between a session and the tidying-up this
        plugin asks for is the trap decision 0017 is about; what is wrong with the command
        is only WHERE it runs."""
        tree, _branch = self.a_tree()
        self.a_live_session(tree)

        proc = self.removing(tree)

        self.assertEqual("deny", self.hook_decision(proc), proc.stdout)
        self.assertFalse(tree.is_dir(), "the gate denied the call and left the tree")

    def test_the_branch_goes_with_it_without_the_session_running_git(self):
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_live_session(tree)

        self.removing(tree)

        self.assertNotIn(branch, self.branches())

    def test_it_says_where_the_shell_is_now_and_that_nothing_is_left(self):
        tree, _branch = self.a_tree()
        self.a_live_session(tree)

        said = self.hook_reason(self.removing(tree))

        self.assertIn(f"cd {self.repo}", said)
        self.assertIn("nothing else to clean up", said)

    def test_a_tree_holding_work_is_kept_and_the_refusal_is_runnable(self):
        """git's own refusal, reported rather than routed around — and the command it names
        runs on this machine, in a tree that is still there because git said no."""
        tree, _branch = self.a_tree()
        self.a_live_session(tree)
        (tree / "unsaved.py").write_text("half a thought\n", encoding="utf-8")

        proc = self.removing(tree)

        self.assertEqual("deny", self.hook_decision(proc))
        self.assertTrue(tree.is_dir(), "a tree with an untracked file was removed")
        self.assertIn("status --porcelain", self.hook_reason(proc))

    def test_force_is_carried_through_because_the_session_typed_it(self):
        tree, _branch = self.a_tree()
        self.a_live_session(tree)
        (tree / "unsaved.py").write_text("half a thought\n", encoding="utf-8")

        self.removing(tree, extra="--force ")

        self.assertFalse(tree.is_dir(), "--force was asked for and not passed on")

    def test_somebody_elses_tree_is_none_of_this(self):
        """The interception is only ever about the tree this session is standing in. Another
        session's tree is the cross-tree rule's business and reaches it unchanged."""
        tree, _branch = self.a_tree()
        self.a_live_session(tree)
        other = self.tmp / "theirs"
        git(["worktree", "add", "-q", "-b", "feat/theirs", str(other)], self.repo)

        self.removing(other)

        self.assertTrue(other.is_dir(), "the gate removed a tree that is not this session's")


class TestFinishingWhatWasLeftBehind(CleanupCase):
    """The state already on the founder's machine: a tree removed by hand before any of
    this existed, with its registration, its branch and its database still here."""

    def test_a_record_for_a_tree_that_is_gone_is_finished_and_forgotten(self):
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        git(["worktree", "remove", str(tree)], self.repo)

        cleaned = worktree.finish_removals(self.ctx())

        self.assertIn(str(tree), cleaned)
        self.assertNotIn(branch, self.branches())
        self.assertIsNone(worktree.mine(self.ctx(), self.session))

    def test_a_tree_still_on_disk_is_left_alone(self):
        tree, _branch = self.a_tree()

        self.assertEqual([], worktree.finish_removals(self.ctx()))
        self.assertTrue(tree.is_dir())

    def test_the_upgrade_runs_it_once_and_says_what_it_did(self):
        from claude_bestpractice import migrate

        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        git(["worktree", "remove", str(tree)], self.repo)

        changed = migrate.repair(self.ctx())

        self.assertTrue(any("0017-finish-removals-done-by-hand" in line for line in changed),
                        changed)
        self.assertEqual([], [line for line in migrate.repair(self.ctx())
                              if "0017-finish-removals" in line])


if __name__ == "__main__":
    unittest.main()
