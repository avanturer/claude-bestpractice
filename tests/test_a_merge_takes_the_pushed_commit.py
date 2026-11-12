"""A merge is judged on the commit it takes, not on the tree the session stands in (#309).

> The merge gate refuses `gh pr merge <n> --admin --squash` after the founder wrote `+merge`.
> 1. Uncommitted files in the session's worktree. They are **not part of the PR**.
> 2. The Stop-gate suite run was interrupted because the founder sent a message. The pushed head
>    had already passed the full suite in the pre-push hook a few minutes earlier.
> The result is a loop. The founder approves, the gate refuses, the session reports, the founder
> writes again, and that message interrupts the next run.

The pre-push hook stamps its green with the tree it ran on. That tree is the evidence for the
commit it pushed, whatever a later run beside a new file said or was stopped from saying.
"""

from __future__ import annotations

from helpers import git
from test_pullrequest import TranscriptCase

from claude_bestpractice import delivery, evidence, store

MERGE = "gh pr merge 48 --admin --squash"


class MergeCase(TranscriptCase):
    def pushed_and_green(self) -> None:
        """A commit on the branch the suite passed on with nothing else in the tree, as the
        pre-push hook records it, and its pull request open with the founder's word on it."""
        self.start()
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["make", "test"])
        self.open_a_pr()
        self.accept()

    # What a 2.12.0 hand-off listed over a stray file, and the status line read from then on.
    STALE = ["there are uncommitted changes: src/next_feature.py"]

    def handed_off_as_2_12_left_it(self) -> None:
        from claude_bestpractice import pullrequest

        self.pushed_and_green()
        record = pullrequest._records(self.ctx())["feat/x"]
        pullrequest.hand_off(self.ctx(), record, self.STALE, "s1")

    def listed(self) -> list:
        from claude_bestpractice import pullrequest

        return pullrequest._records(self.ctx())["feat/x"].get("blockers")

    def green_as_2_12_wrote_it(self) -> None:
        """The branch's green as 2.12.0 wrote it: its one tree, no list, that gate's stamp."""
        path = evidence._green_path(self.ctx())
        green = store.read_json(path, default={})
        store.write_json(path, {key: value for key, value in green.items()
                                if key != "passed_trees"} | {"gate": "2.12.0"})

    def the_next_pr_begun(self) -> None:
        """New work for the next pull request, never committed, beside the pushed commit."""
        self.write("src/next_feature.py", "def later():\n    return 1\n")

    def stopped_by_the_founder(self) -> None:
        """The Stop gate's run, on the tree as it stands, stopped when the founder wrote."""
        evidence.record_cut_short(self.ctx(), ["pytest"])

    def merge(self):
        return self.tool("Bash", {"command": MERGE})

    def assert_merges(self) -> None:
        proc = self.merge()
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def refused(self) -> str:
        proc = self.merge()
        self.assertEqual("deny", self.decision(proc), "the merge went through")
        return self.reason(proc)


class TestTheIssuesOwnSequence(MergeCase):
    def test_a_new_file_and_a_run_the_founder_stopped_do_not_hold_a_pushed_commit_that_passed(self):
        self.pushed_and_green()
        self.the_next_pr_begun()
        self.stopped_by_the_founder()
        self.assert_merges()

    def test_a_pass_beside_the_new_file_does_not_take_the_commits_own_green_away(self):
        """One green per branch, overwritten by every pass: a Stop that passed with the new file
        in the tree, which hashes to no tree, replaced the pushed commit's green."""
        self.pushed_and_green()
        self.the_next_pr_begun()
        evidence.record_green(self.ctx(), ["pytest"])
        self.assertEqual("", evidence.last_green(self.ctx())["tree"], "the fixture proves nothing")
        self.assert_merges()

    def test_what_a_2_12_session_left_on_disk_merges_under_this_gate(self):
        """Records exactly as 2.12.0 wrote them: a green carrying its one tree and no list, and
        the stopped run. A session that hit the loop there merges once the plugin is updated,
        with nothing to repair."""
        self.pushed_and_green()
        self.green_as_2_12_wrote_it()
        self.the_next_pr_begun()
        self.stopped_by_the_founder()
        run = store.read_json(evidence._run_path(self.ctx()), default={})
        self.assertEqual(("", True), (run.get("tree"), run.get("cut_short")), "the fixture")
        self.assert_merges()


class TestWhatStillHoldsAMerge(MergeCase):
    def test_an_edit_to_the_pull_requests_own_file_left_uncommitted(self):
        self.pushed_and_green()
        self.write("src/app.py", "x = 2\n")
        said = self.refused()
        self.assertIn("files this pull request changes have uncommitted changes: src/app.py", said)
        self.assertNotIn("next_feature", said)

    def test_a_commit_nothing_ran_on_is_still_held_by_the_run_that_was_stopped(self):
        """Decision 0044's reason stands: `+merge` typed during the run must not merge, on an
        older green, a change nobody ran (#277)."""
        self.pushed_and_green()
        self.write("src/app.py", "x = 3\n")
        self.commit("a change after the green")
        self.stopped_by_the_founder()
        self.assertIn(evidence.STOPPED_RUN, self.refused())

    def test_a_green_only_of_the_tree_with_the_new_file_in_it_does_not_vouch_for_the_commit(self):
        """The file could be what made it pass: a committed test importing an uncommitted
        module is green here and red on the trunk."""
        self.start()
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        self.the_next_pr_begun()
        evidence.record_green(self.ctx(), ["make", "test"])
        self.open_a_pr()
        self.accept()
        said = self.refused()
        self.assertIn("there are uncommitted changes: src/next_feature.py", said)
        self.assertIn("git stash push --include-untracked", said)
        self.assertIn("git stash pop", said)

    def test_a_failure_is_a_verdict_and_still_holds_it(self):
        self.pushed_and_green()
        self.the_next_pr_begun()
        evidence.record_run(self.ctx(), ["pytest"], passed=False)
        self.assertIn("FAILED", self.refused())


class TestWhatTheSessionClearsIsNotPutToTheFounder(MergeCase):
    """Told to take a stopped run to the founder, the session did; their answer stopped the next
    run, and the merge they had accepted was refused round and round."""

    def test_a_stray_file_and_a_stopped_run_are_the_sessions_to_clear(self):
        self.start()
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        self.the_next_pr_begun()
        self.open_a_pr()
        self.accept()
        self.stopped_by_the_founder()
        said = self.refused()
        self.assertIn("None of them is the founder's to decide", said)
        self.assertNotIn("Tell the founder exactly this and stop", said)

    def test_anything_the_founder_decides_keeps_them_in_it(self):
        self.pushed_and_green()
        self.write("src/app.py", "x = 2\n")
        store.append_jsonl(store.tier_b(self.ctx(), "unverified.jsonl"), {
            "session_id": "s1", "branch": "feat/x", "head": self.ctx().head,
            "reason": "the suite printed no counts", "recorded_at": 0,
        })
        said = self.refused()
        self.assertIn("Tell the founder exactly this and stop", said)


class TestTheTreesASuitePassedOnAreKept(MergeCase):
    def test_newest_last_and_bounded(self):
        ctx = self.ctx()
        trees = []
        for n in range(evidence.PASSED_TREES_KEPT + 3):
            self.write("src/app.py", f"x = {n}\n")
            self.commit(f"change {n}")
            trees.append(git(["rev-parse", "HEAD^{tree}"], self.repo))
            evidence.record_green(ctx, ["make", "test"])
        kept = evidence.last_green(ctx)["passed_trees"]
        self.assertEqual(trees[-evidence.PASSED_TREES_KEPT:], kept)
        self.assertTrue(evidence.suite_passed_on(ctx, trees[-1]))
        self.assertFalse(evidence.suite_passed_on(ctx, trees[0]))
        # A pass on a tree with uncommitted work in it is stamped as no tree, and "no tree"
        # is never a tree the suite passed on: a merge whose commit git cannot name gets none.
        self.the_next_pr_begun()
        evidence.record_green(ctx, ["make", "test"])
        self.assertEqual("", evidence.last_green(ctx)["tree"], "the fixture")
        self.assertFalse(evidence.suite_passed_on(ctx, ""))
        self.assertTrue(evidence.suite_passed_on(ctx, trees[-1]), "kept past the dirty pass")

    def test_the_tree_a_merge_takes_is_the_pushed_head(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("one")
        here = git(["rev-parse", "HEAD^{tree}"], self.repo)
        self.assertEqual(here, delivery.merged_tree(self.ctx(), "feat/x"))
        git(["update-ref", "refs/remotes/origin/feat/x", "HEAD~1"], self.repo)
        self.assertEqual(git(["rev-parse", "HEAD~1^{tree}"], self.repo),
                         delivery.merged_tree(self.ctx(), "feat/x"))
        self.assertEqual("", delivery.merged_tree(self.ctx(), "no/such-branch"))

    def test_a_scoped_suites_pass_keeps_the_repositorys_trees(self):
        from claude_bestpractice import suites

        self.write("src/app.py", "x = 1\n")
        self.commit("one")
        tree = git(["rev-parse", "HEAD^{tree}"], self.repo)
        evidence.record_green(self.ctx(), ["make", "test"])
        evidence.record_green(self.ctx(), ["jest"],
                              suites.Suite(path="mobile/", command=("jest",), declared=True))
        self.assertEqual("mobile/", evidence.last_green(self.ctx())["path"], "the fixture")
        self.assertTrue(evidence.suite_passed_on(self.ctx(), tree))


class TestARepairThatUnstampsGreensTakesTheListToo(MergeCase):
    """Repair 0029 drops every tree stamp a green carried before `tree_hash` was fixed; the
    trees a suite passed on are stamps of the same kind, and go with it."""

    def test_neither_stamp_is_left(self):
        from claude_bestpractice import migrate

        self.write("src/app.py", "x = 1\n")
        self.commit("one")
        evidence.record_green(self.ctx(), ["make", "test"])
        self.assertTrue(evidence.last_green(self.ctx())["passed_trees"], "the fixture")
        migrate.repair(self.ctx())
        green = evidence.last_green(self.ctx())
        self.assertEqual((None, None), (green.get("tree"), green.get("passed_trees")))


class TestWhatTheStatusLineSaysIsJudgedAgain(MergeCase):
    """A pull request is handed off once, and its hand-off's list was the founder's status line
    for good: "blocked" over a pull request nothing stood in the way of any more."""

    def test_the_first_session_start_after_the_upgrade_judges_it_again(self):
        from claude_bestpractice import migrate

        self.handed_off_as_2_12_left_it()
        self.assertEqual(self.STALE, self.listed(), "the fixture")
        step = migrate._REPAIRS["0048-judge-open-pull-requests-again"][1]
        self.assertIn("1 open pull request(s)", step(self.ctx()))
        self.assertEqual([], self.listed())
        self.assertEqual("", step(self.ctx()), "nothing changed, nothing written")

    def test_the_stop_after_a_turn_judges_it_again(self):
        self.handed_off_as_2_12_left_it()
        self.gate("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop",
                                    "stop_hook_active": False})
        self.assertEqual([], self.listed())

    def test_a_merge_refused_writes_what_it_was_refused_over(self):
        self.handed_off_as_2_12_left_it()
        self.write("src/app.py", "x = 2\n")
        said = self.refused()
        self.assertIn("files this pull request changes", said)
        self.assertEqual(1, len(self.listed()))
        self.assertIn("files this pull request changes", self.listed()[0])


class TestASessionCaughtInTheLoopComesOutOfItAfterTheUpgrade(MergeCase):
    """The founder's own question about the loop: does a session that hit it on the old version
    still hang once the plugin is updated and VS Code restarted? A restart resumes the chat, its
    SessionStart says `resume`, and the repairs this version brings run there."""

    def test_the_resumed_chat_sees_it_ready_and_merges_it(self):
        from claude_bestpractice import board, migrate

        self.handed_off_as_2_12_left_it()
        self.green_as_2_12_wrote_it()
        ctx = self.ctx()
        self.the_next_pr_begun()
        self.stopped_by_the_founder()
        done = store.tier_b(ctx, migrate.LEDGER)
        ran = store.read_json(done, default={})
        ran.pop("0048-judge-open-pull-requests-again", None)
        store.write_json(done, ran)
        self.assertIn("1 pull request(s) blocked", board.glance(ctx, "s1")["line"], "the fixture")

        self.gate("session-start", {"session_id": "s1", "hook_event_name": "SessionStart",
                                    "source": "resume"})

        self.assertIn("wait for your `+merge`", board.glance(self.ctx(), "s1")["line"])
        self.assert_merges()
