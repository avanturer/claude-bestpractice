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

from helpers import RepoCase, add_origin, git
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

    def test_a_branch_git_kept_is_said_to_be_kept(self):
        """"…is removed and so is <branch>. There is nothing else to clean up." was said over
        a branch `git branch` still listed, carrying the one commit nobody had merged."""
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.a_live_session(tree)

        said = self.hook_reason(self.removing(tree))

        self.assertFalse(tree.is_dir(), "precondition: the tree itself was removable")
        self.assertIn(branch, self.branches())
        self.assertIn(f"{branch} is kept", said)
        self.assertNotIn("and so is", said)
        self.assertNotIn("nothing else to clean up", said)

    def test_a_tree_switched_onto_the_trunk_leaves_the_trunk(self):
        """The clone's own `main` is trivially in the trunk, so a proof by ancestry would
        have taken it with a tree the session had switched onto it."""
        tree, _branch = self.a_tree()
        git(["switch", "-q", "-c", "elsewhere"], self.repo)
        git(["switch", "-q", "main"], tree)
        self.a_live_session(tree)

        said = self.hook_reason(self.removing(tree))

        self.assertFalse(tree.is_dir(), "precondition: the tree itself was removable")
        self.assertIn("main", self.branches().split())
        self.assertNotIn("so is main", said)

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


class TestACheckpointDoesNotPinItsTree(TreeCase):
    """A compaction checkpoint was written into the tree the session stood in — the hook's
    working directory follows the session into its worktree — as an untracked file, and
    `git worktree remove` refuses a tree holding one for good: the reaper never cleared it,
    the finished tree never removed itself, and nothing named it, because `stranded()`
    exempts `.claude/`."""

    HARNESS = "4f1c2a9e-7b3d-4e8f-9a1b-2c3d4e5f6a7b"

    def compacted_in(self, tree) -> None:
        proc = self.run_hook("checkpoint", {"session_id": self.HARNESS, "trigger": "auto",
                                            "hook_event_name": "PreCompact"}, cwd=tree)
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_a_compaction_in_the_tree_leaves_it_clean(self):
        from claude_bestpractice import store

        tree, _branch = self.a_tree()

        self.compacted_in(tree)

        self.assertEqual("", git(["status", "--porcelain", "--untracked-files=all"], tree))
        self.assertTrue(list(store.checkpoint_dir(self.ctx()).glob("*.md")))

    def test_the_session_still_gets_it_back(self):
        tree, _branch = self.a_tree()
        self.compacted_in(tree)

        said = self.run_hook("session-start", {"session_id": self.HARNESS, "source": "compact",
                                               "hook_event_name": "SessionStart"}, cwd=tree)

        self.assertIn("RESTORED AFTER COMPACTION", said.stdout)

    def test_one_an_earlier_version_left_in_a_tree_is_carried_out(self):
        from claude_bestpractice import migrate, store

        tree, _branch = self.a_tree()
        left = tree / ".claude" / "claude-bestpractice" / "checkpoints"
        left.mkdir(parents=True)
        (left / f"20260901-120000-{self.HARNESS[:8]}.md").write_text(
            "---\nkind: checkpoint\n---\nwhat the window held\n", encoding="utf-8")

        migrate.repair(self.ctx())

        self.assertEqual("", git(["status", "--porcelain", "--untracked-files=all"], tree))
        self.assertIn("what the window held", store.newest_checkpoint(self.ctx(), self.HARNESS))


class TestABranchGoesOnlyOnItsOwnProof(RepoCase):
    """What the sweep deleted was decided by `git branch -d`, which answers whether a branch
    is merged into its UPSTREAM — or into HEAD when it has none — and by a proof computed for
    a different branch. Never by whether that branch's work was in the trunk.

    So a clone with a real `origin`, where upstreams exist, and the sweep reached the way it
    is in use: a tree this plugin made for a session that is gone, and the next session start.
    """

    def setUp(self) -> None:
        super().setUp()
        add_origin(self.repo, self.tmp)

    def a_branch(self, name: str, relpath: str, mode: int = 0o644) -> str:
        """One commit of work on its own branch, and main left where it was."""
        git(["switch", "-q", "-c", name], self.repo)
        self.write(relpath, "work\n").chmod(mode)
        self.commit(f"the {name} work")
        git(["switch", "-q", "main"], self.repo)
        return name

    def the_next_session_starts(self) -> None:
        """A dead session's tree to reap, which is what runs the branch sweep."""
        worktree.provision(self.ctx(), "tiny docs fix", "gone-session")
        self.run_hook("session-start", {"session_id": "next", "hook_event_name": "SessionStart",
                                        "source": "startup"})

    def branches(self) -> list[str]:
        return git(["branch", "--format=%(refname:short)"], self.repo).split()

    def test_a_pushed_branch_with_its_pull_request_open_stays(self):
        branch = self.a_branch("feat/payments-wip", "pay.py")
        git(["push", "-q", "-u", "origin", branch], self.repo)

        self.the_next_session_starts()

        self.assertIn(branch, self.branches(), "deleted for being merged into its own upstream")

    def test_a_mode_change_is_not_already_in_the_trunk(self):
        """Same blob, different mode: comparing blob ids alone called it delivered."""
        self.write("deploy.sh", "work\n")
        self.commit("the deploy script")
        git(["push", "-q", "origin", "main"], self.repo)
        branch = self.a_branch("fix/deploy-exec", "deploy.sh", mode=0o755)

        self.the_next_session_starts()

        self.assertIn(branch, self.branches(), "`-D` took the only commit making it executable")

    def test_a_branch_merged_upstream_goes_while_this_main_lags(self):
        """Merged on the remote and never pulled here: `-d` asked this checkout's HEAD."""
        branch = self.a_branch("feat/add-search", "search.py")
        git(["push", "-q", "origin", f"{branch}:main"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)

        self.the_next_session_starts()

        self.assertNotIn(branch, self.branches())

    def test_the_branch_a_tree_was_made_on_is_not_taken_on_another_branch_s_proof(self):
        """The reaper proved the branch the tree stood on NOW and force-deleted the one it
        was MADE on — unmerged work, gone, under a line saying branches are kept."""
        tree = worktree.provision(self.ctx(), "add the importer", "dead-session")
        made_on = git(["rev-parse", "--abbrev-ref", "HEAD"], tree)
        (tree / "importer.py").write_text("half an importer\n", encoding="utf-8")
        git(["add", "-A"], tree)
        git(["commit", "-qm", "WIP importer"], tree)
        git(["switch", "-q", "-c", "fix/typo", "origin/main"], tree)
        (tree / "README.md").write_text("seed, fixed\n", encoding="utf-8")
        git(["commit", "-qam", "fix the typo"], tree)
        self.write("README.md", "seed, fixed\n")
        self.commit("fix the typo (#12)")
        git(["push", "-q", "origin", "main"], self.repo)

        self.run_hook("session-start", {"session_id": "next", "hook_event_name": "SessionStart",
                                        "source": "startup"})

        self.assertFalse(tree.is_dir(), "precondition: the abandoned tree was reaped")
        self.assertIn(made_on, self.branches())
        self.assertNotIn("fix/typo", self.branches(), "the squash-merged one still goes")


if __name__ == "__main__":
    unittest.main()
