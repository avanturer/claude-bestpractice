"""Issue #220: isolation was solved and CLOSING the work was not.

Measured on a repository with three to eight parallel sessions: thirty-eight worktrees
besides the main checkout, fifteen of them standing over a branch already merged into
main, twenty-eight without a commit in seven days, and a hundred and thirty-five local
branches of which thirty-one were merged and undeleted. The rule that said to remove a
tree after its merge was written in that project's own instructions the whole time.

So the founder's instruction, in their words: "когда из ворктри уже все замерджили и
модель даже ВСЕ свои задачи закрыла то она сама его удаляла, так ничего мы не теряем и
меня не будет тыркать она с разрешением и засариваться не будет".
"""

from __future__ import annotations

import re
import unittest

from helpers import RepoCase, git, sid

from claude_bestpractice import plan, pullrequest, worktree


class TreeCase(RepoCase):
    """A repository with a trunk ref, and one tree this plugin provisioned for one session."""

    def setUp(self) -> None:
        super().setUp()
        self.session = sid(self.repo, "s1")
        self.write("src/app.py", "x = 1\n")
        self.commit("a history to branch from")
        self.trunk()

    def trunk(self) -> None:
        """A local ref literally named `origin/main`, which is what the code resolves."""
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)

    def a_tree(self, task: str = "the work") -> tuple:
        tree = worktree.provision(self.ctx(), task, self.session)
        self.assertIsNotNone(tree, "precondition: the plugin provisioned a tree")
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], tree)
        self.assertEqual("", git(["status", "--porcelain"], tree),
                         "precondition: a fresh tree is clean")
        return tree, branch

    def work_in(self, tree, name: str = "src/feature.py") -> None:
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("y = 2\n", encoding="utf-8")
        git(["add", "-A"], tree)
        git(["commit", "-qm", "the work"], tree)

    def merge(self, branch: str, squash: bool = False) -> None:
        if squash:
            git(["merge", "--squash", branch], self.repo)
            git(["commit", "-qm", f"squash {branch}"], self.repo)
        else:
            git(["merge", "--no-ff", "-q", "-m", f"merge {branch}", branch], self.repo)
        self.trunk()

    def a_closed_card(self, paths=("src/feature.py",)) -> None:
        task = plan.add(self.ctx(), "the work", paths=list(paths), done_when="stated")
        plan.claim(self.ctx(), task.id, self.session, "feat/the-work")
        plan.complete(self.ctx(), task.id)


class TestAFinishedTreeRemovesItself(TreeCase):
    def test_merged_work_and_a_clear_board_takes_the_tree_and_the_branch(self):
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()

        gone = worktree.release_mine(self.ctx(), self.session)

        self.assertEqual((str(tree), branch), gone[:2])
        self.assertFalse(tree.is_dir(), "the tree is still on disk")
        self.assertNotIn(branch, git(["branch", "--format=%(refname:short)"], self.repo))

    def test_a_squash_merge_takes_the_branch_too(self):
        """What `git branch -d` cannot see. A squash rewrites the commits, so the branch tip
        is an ancestor of nothing — and squash is how this repository's own merges land, so
        `-d` refusing would leave a branch behind every single time."""
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch, squash=True)
        self.a_closed_card()

        self.assertEqual((str(tree), branch),
                         worktree.release_mine(self.ctx(), self.session)[:2])
        self.assertNotIn(branch, git(["branch", "--format=%(refname:short)"], self.repo))

    def test_the_registry_entry_goes_with_it(self):
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        worktree.release_mine(self.ctx(), self.session)
        self.assertIsNone(worktree.mine(self.ctx(), self.session))

    def test_the_next_write_is_sent_to_a_fresh_tree(self):
        """The cost this accepts, stated as a test: the session gets a NEW tree, cut from a
        trunk that has moved on, rather than the stale one it just finished in."""
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        worktree.release_mine(self.ctx(), self.session)

        again = worktree.provision(self.ctx(), "the next thing", self.session)
        self.assertTrue(again.is_dir())
        self.assertNotEqual(branch, git(["rev-parse", "--abbrev-ref", "HEAD"], again))


class TestTheStopGateIsWhereItHappens(TreeCase):
    """Not the sweep that runs when the session is already dead — the turn that finishes
    the work. The founder's complaint is not that trees are never collected; it is that
    they pile up while they are being worked in, and that being asked is the other half."""

    def a_finished_session(self):
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        record = self.session_record("s1")
        record.worktree = tree.as_posix()
        from claude_bestpractice import sessions
        sessions.register(self.ctx(), record)
        return tree, branch

    def stop(self):
        return self.run_hook("evidence-gate", {
            "session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False,
        })

    def test_the_turn_that_finishes_the_work_puts_the_tree_away(self):
        tree, _branch = self.a_finished_session()
        proc = self.stop()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertFalse(tree.is_dir(), f"the tree survived the Stop gate: {proc.stdout}")

    def test_it_says_so_and_says_where_the_shell_is_now(self):
        """A directory that is simply gone reads as lost work, and a shell standing in one
        fails on `getcwd` at the next call. Both are answered in the same line."""
        import json

        tree, _branch = self.a_finished_session()
        said = json.loads(self.stop().stdout or "{}").get("systemMessage", "")
        self.assertIn(str(tree), said)
        self.assertIn(f"cd {self.repo}", said)

    def test_switched_off_it_leaves_the_tree(self):
        self.configure(remove_finished_trees=False)
        self.commit("the founder's switch")
        tree, _branch = self.a_finished_session()
        self.stop()
        self.assertTrue(tree.is_dir())


class TestWhatKeepsATreeStanding(TreeCase):
    def test_unmerged_commits_keep_it(self):
        tree, _branch = self.a_tree()
        self.work_in(tree)
        self.a_closed_card()
        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue(tree.is_dir())

    def test_one_untracked_file_keeps_it(self):
        """git's own test, and the reason this is safe: `git worktree remove` without
        `--force` refuses a tree with anything in it, so the conditions here only decide
        whether to ASK."""
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        (tree / "notes.txt").write_text("half an idea\n", encoding="utf-8")

        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue(tree.is_dir())
        self.assertTrue((tree / "notes.txt").is_file(), "it deleted somebody's file")

    def test_an_uncommitted_change_keeps_it(self):
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        (tree / "src" / "feature.py").write_text("y = 3  # still working\n", encoding="utf-8")
        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue(tree.is_dir())

    def test_a_card_still_in_flight_keeps_it(self):
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        second = plan.add(self.ctx(), "more to do", paths=["src/app.py"], done_when="stated")
        plan.claim(self.ctx(), second.id, self.session, branch)

        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue(tree.is_dir())

    def test_a_paused_card_on_this_branch_keeps_it(self):
        """Pausing clears the owner by design, so "nothing is mine" is true of a card this
        session parked ten seconds ago. Read by branch for exactly that reason."""
        tree, branch = self.a_tree()
        self.work_in(tree)
        self.merge(branch)
        self.a_closed_card()
        parked = plan.add(self.ctx(), "half done", paths=["src/app.py"], done_when="stated")
        plan.claim(self.ctx(), parked.id, self.session, branch)
        plan.pause(self.ctx(), parked.id, "waiting on the founder")

        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue(tree.is_dir())

    def test_a_session_that_has_closed_nothing_keeps_it(self):
        """An empty branch is trivially merged, so without this the tree a session was just
        given would be taken away before it wrote a line in it."""
        tree, _branch = self.a_tree()
        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue(tree.is_dir())

    def test_the_main_checkout_is_never_removed(self):
        """The one directory in the clone that must outlive every session: the ledger, the
        config and the Tier A records all live in it."""
        self.a_closed_card()
        worktree.record(self.ctx(), "main-as-a-tree", str(self.repo), "main", True, self.session)
        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue((self.repo / "src" / "app.py").is_file())

    def test_a_tree_this_plugin_did_not_make_is_left_alone(self):
        outside = self.add_worktree("somebody-elses-tree")
        self.a_closed_card()
        self.assertIsNone(worktree.release_mine(self.ctx(), self.session))
        self.assertTrue(outside.is_dir())

    def test_the_founder_can_switch_it_off(self):
        """`remove_finished_trees: false` leaves the tree standing and the line naming it."""
        from claude_bestpractice import config

        self.configure(remove_finished_trees=False)
        self.assertFalse(config.load(self.ctx()).remove_finished_trees)


if __name__ == "__main__":
    unittest.main()


class TestTheBoardNamesWhatNobodyWillComeBackTo(TreeCase):
    """The other half of #220's first two sections: what the plugin may NOT remove, it says
    out loud — a tree another tool made, a branch that never became a pull request, a pull
    request nobody has touched in a month."""

    def a_tree_nobody_made_for_us(self, name: str = "hand-rolled") -> object:
        tree = self.add_worktree(name)
        (tree / "src").mkdir(parents=True, exist_ok=True)
        (tree / "src" / "hand.py").write_text("z = 3\n", encoding="utf-8")
        git(["add", "-A"], tree)
        git(["commit", "-qm", "by hand"], tree)
        git(["merge", "--no-ff", "-q", "-m", f"merge {name}", name], self.repo)
        self.trunk()
        return tree

    def test_a_merged_tree_that_is_not_ours_is_named(self):
        tree = self.a_tree_nobody_made_for_us()
        found = worktree.needs_a_decision(self.ctx())
        self.assertEqual([(str(tree), "hand-rolled", "")], found)

    def test_a_detached_tree_is_named_with_no_branch(self):
        """Its commits are on no branch, so the directory is the only thing holding them."""
        tree = self.add_worktree("was-a-branch")
        git(["checkout", "-q", "--detach"], tree)
        self.assertEqual([(str(tree), "", "")], worktree.needs_a_decision(self.ctx()))

    def test_an_unmerged_tree_is_not(self):
        tree = self.add_worktree("still-working")
        (tree / "wip.py").write_text("q = 1\n", encoding="utf-8")
        git(["add", "-A"], tree)
        git(["commit", "-qm", "wip"], tree)
        self.assertEqual([], worktree.needs_a_decision(self.ctx()))
        self.assertTrue(tree.is_dir())

    def test_a_tree_a_live_session_is_in_is_not(self):
        from claude_bestpractice import sessions

        tree = self.a_tree_nobody_made_for_us("occupied")
        record = self.session_record("s2")
        record.worktree = tree.as_posix()
        sessions.register(self.ctx(), record)
        self.assertEqual([], worktree.needs_a_decision(self.ctx()))

    def test_the_main_checkout_is_never_named(self):
        self.assertEqual([], worktree.needs_a_decision(self.ctx()))

    def test_naming_them_is_all_it_does(self):
        tree = self.a_tree_nobody_made_for_us()
        worktree.needs_a_decision(self.ctx())
        self.assertTrue(tree.is_dir(), "it removed a tree this plugin did not make")


class TestBranchesAndPullRequestsNothingIsMoving(TreeCase):
    def a_branch_with_commits(self, name: str, hours_ago: float = 48.0) -> None:
        """A branch whose last commit is `hours_ago` old, committer date included — which is
        the date `for-each-ref` reports and therefore the one under test."""
        import os
        import time

        when = time.strftime("%Y-%m-%dT%H:%M:%S+0000",
                             time.gmtime(time.time() - hours_ago * 3600))
        git(["checkout", "-q", "-b", name], self.repo)
        self.write("src/orphan.py", f"# {name}\n")
        git(["add", "-A"], self.repo)
        os.environ["GIT_COMMITTER_DATE"] = when
        try:
            git(["commit", "-qm", f"work on {name}", f"--date={when}"], self.repo)
        finally:
            os.environ.pop("GIT_COMMITTER_DATE", None)
        git(["checkout", "-q", "main"], self.repo)

    def test_a_branch_with_commits_and_no_pull_request_is_named(self):
        self.a_branch_with_commits("chore/untrack-ledger", hours_ago=96)
        found = pullrequest.branches_without_one(self.ctx(), hours=12)
        self.assertEqual(["chore/untrack-ledger"], [name for name, _age in found])

    def test_a_branch_whose_pull_request_is_recorded_is_not(self):
        self.a_branch_with_commits("feat/has-a-pr", hours_ago=96)
        pullrequest.opened(self.ctx(), "feat/has-a-pr", "main", "s1", number=7)
        self.assertEqual([], pullrequest.branches_without_one(self.ctx(), hours=12))

    def test_a_branch_from_ten_minutes_ago_is_left_alone(self):
        self.a_branch_with_commits("feat/just-started", hours_ago=0)
        self.assertEqual([], pullrequest.branches_without_one(self.ctx(), hours=12))

    def test_a_merged_branch_is_not_unfinished(self):
        self.a_branch_with_commits("feat/landed", hours_ago=96)
        git(["merge", "--no-ff", "-q", "-m", "merge", "feat/landed"], self.repo)
        self.trunk()
        self.assertEqual([], pullrequest.branches_without_one(self.ctx(), hours=12))

    def test_zero_hours_switches_the_question_off(self):
        self.a_branch_with_commits("chore/whatever", hours_ago=96)
        self.assertEqual([], pullrequest.branches_without_one(self.ctx(), hours=0))

    def test_a_month_old_pull_request_is_named(self):
        import time

        import json

        from claude_bestpractice import store

        pullrequest.opened(self.ctx(), "feat/forgotten", "main", "s1", number=542)
        path = store.tier_b(self.ctx(), pullrequest.PR_FILE)
        body = json.loads([line for line in path.read_text(encoding="utf-8").splitlines()
                           if line.strip()][-1])
        body["opened_at"] = time.time() - 20 * 86400
        path.write_text(json.dumps(body) + "\n", encoding="utf-8")

        found = pullrequest.idle(self.ctx(), days=7)
        self.assertEqual([542], [number for number, _branch, _days in found])

    def test_a_pull_request_opened_today_is_not(self):
        pullrequest.opened(self.ctx(), "feat/fresh", "main", "s1", number=600)
        self.assertEqual([], pullrequest.idle(self.ctx(), days=7))


class TestTheBoardSaysIt(TreeCase):
    def board(self) -> str:
        import json

        proc = self.run_hook("session-start", {
            "session_id": "s9", "hook_event_name": "SessionStart", "source": "startup",
        })
        payload = json.loads(proc.stdout or "{}")
        return payload.get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_the_merged_tree_is_on_the_board_with_the_command(self):
        tree = self.add_worktree("left-behind")
        git(["merge", "--no-ff", "-q", "-m", "merge", "left-behind"], self.repo)
        self.trunk()
        body = self.board()
        self.assertIn("TREES NOBODY IS IN", body)
        self.assertIn("left-behind", body)
        self.assertIn("worktree remove", body)
        self.assertTrue(tree.is_dir())

    def test_a_quiet_repository_says_neither_thing(self):
        body = self.board()
        self.assertNotIn("TREES NOBODY IS IN", body)
        self.assertNotIn("NOBODY IS COMING BACK", body)

    def test_a_tree_deleted_by_hand_is_named_with_the_prune_that_forgets_it(self):
        """It read as a DETACHED HEAD, and `git -C <path> log` died on `cannot change to`:
        a command named on the board that cannot run here (decision 0020)."""
        import shutil
        import subprocess

        tree = self.add_worktree("deleted-by-hand")
        shutil.rmtree(tree)

        body = self.board()

        self.assertNotIn("DETACHED HEAD", body)
        named = re.search(r"`(git -C \S+ worktree prune)`", body)
        self.assertIsNotNone(named, body)
        ran = subprocess.run(named.group(1).split(), capture_output=True, text=True)
        self.assertEqual(0, ran.returncode, ran.stderr)
        self.assertNotIn("deleted-by-hand", git(["worktree", "list"], self.repo))

    def test_a_locked_tree_is_not_offered_for_removal(self):
        """Claude Code locks the tree of every agent it is running; `worktree remove` on it
        exits 128, and a lock set by hand says to leave it."""
        tree = self.add_worktree("an-agent-is-in-it")
        git(["worktree", "lock", "--reason", "claude agent agent-a1b2", str(tree)], self.repo)

        self.assertEqual([], worktree.needs_a_decision(self.ctx()))
        self.assertNotIn("TREES NOBODY IS IN", self.board())

    def test_a_tree_holding_a_submodule_is_named_with_what_git_takes(self):
        import subprocess

        from helpers import make_repo

        library = make_repo(self.tmp, "library")
        tree = self.add_worktree("with-a-submodule")
        git(["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library),
             "vendor/library"], tree)
        git(["commit", "-qm", "vendor the library"], tree)
        git(["merge", "--no-ff", "-q", "-m", "merge", "with-a-submodule"], self.repo)
        self.trunk()

        found = worktree.needs_a_decision(self.ctx())

        self.assertEqual([(str(tree), "with-a-submodule", worktree.SUBMODULES)], found)
        self.assertIn("--force", self.board())
        plain = subprocess.run(["git", "worktree", "remove", str(tree)], cwd=str(self.repo),
                               capture_output=True, text=True)
        self.assertNotEqual(0, plain.returncode, "the fixture proves nothing: git took it")


class TestTheSweepFinishesTheJobToo(TreeCase):
    """The sweep that runs when a session is already dead had the same blind spot: a branch
    merged by squash is an ancestor of nothing, so `git branch -d` refused it and every
    squash merge left a branch behind (#220)."""

    def test_a_dead_sessions_squash_merged_branch_is_deleted(self):
        tree = worktree.provision(self.ctx(), "work that shipped", "dead-session")
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], tree)
        self.work_in(tree)
        self.merge(branch, squash=True)

        self.assertEqual([str(tree)], worktree.reap_unused(self.ctx(), live=set()))
        self.assertNotIn(branch, git(["branch", "--format=%(refname:short)"], self.repo))

    def test_an_unmerged_branch_still_survives_its_session(self):
        tree = worktree.provision(self.ctx(), "work nobody merged", "dead-session")
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], tree)
        self.work_in(tree)

        self.assertEqual([str(tree)], worktree.reap_unused(self.ctx(), live=set()))
        self.assertIn(branch, git(["branch", "--format=%(refname:short)"], self.repo),
                      "it deleted a branch nobody merged")


class TestABranchSomebodyIsOnIsNotAbandoned(TreeCase):
    def test_a_live_sessions_branch_is_never_named(self):
        """The line says nobody is coming back to these. Somebody is on that one now."""
        from claude_bestpractice import sessions

        git(["checkout", "-q", "-b", "feat/in-progress"], self.repo)
        self.write("src/wip.py", "x = 1\n")
        self.commit("work in progress")
        git(["checkout", "-q", "main"], self.repo)
        record = self.session_record(self.session)
        record.branch = "feat/in-progress"
        sessions.register(self.ctx(), record)

        found = pullrequest.branches_without_one(self.ctx(), hours=0.0001)
        self.assertEqual([], [name for name, _age in found])
