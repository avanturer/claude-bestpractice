"""What the plugin's logs keep: a pull request GitHub merged is settled by the number it wrote
into the trunk, and every log is cut down to what can still be read (decision 0056).

Measured on a founder's clone: sixteen of its eighteen open pull-request records were merged,
the pull-request log held 1,016 rows for 281 branches, the open items 1,298 rows for 848 items,
and 256 of 290 green records were about branches deleted after their merge.
"""

from __future__ import annotations

import json
import os
import threading
import time
from unittest import mock

from helpers import RepoCase, git

from claude_bestpractice import board, evidence, migrate, plan, pullrequest, store

DAY = 24 * 3600


class LogCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        # Work of its own, or the trunk's history holds its tip and it reads as merged anyway.
        git(["checkout", "-q", "-b", "feat/x"], self.repo)
        self.write("src/x.py", "x = 1\n")
        self.commit("the branch's own work")
        git(["checkout", "-q", "main"], self.repo)

    def trunk_says(self, subject: str) -> None:
        """A commit on the trunk as GitHub writes a merge into it, fetched."""
        self.write("src/merged.py", f"# {subject}\n")
        self.commit(subject)
        git(["update-ref", "refs/remotes/origin/main", "HEAD"], self.repo)

    def state_of(self, branch: str) -> str:
        return str(pullrequest._records(self.ctx())[branch]["state"])

    def rows(self, name: str) -> list:
        return store.read_jsonl(store.tier_b(self.ctx(), name))


class TestTheTrunkSaysWhatMerged(LogCase):
    def test_a_squash_naming_its_number_settles_it_as_merged(self):
        pullrequest.opened(self.ctx(), "feat/x", "main", "s1", number=41)
        self.trunk_says("Add the thing (#41)")

        self.assertEqual(["feat/x"], pullrequest.reconcile(self.ctx()))
        self.assertEqual(pullrequest.MERGED, self.state_of("feat/x"))

    def test_a_merge_commit_naming_its_number_settles_it_as_merged(self):
        pullrequest.opened(self.ctx(), "feat/x", "main", "s1", number=41)
        self.trunk_says("Merge pull request #41 from someone/feat-x")

        pullrequest.reconcile(self.ctx())
        self.assertEqual(pullrequest.MERGED, self.state_of("feat/x"))

    def test_a_number_mentioned_anywhere_else_settles_nothing(self):
        pullrequest.opened(self.ctx(), "feat/x", "main", "s1", number=41)
        for subject in ("Fix (#41) handling of sizes", 'Revert "Add the thing (#41)"',
                        "Add the thing (#410)", "Merge pull request #410 from someone/y"):
            with self.subTest(subject=subject):
                self.trunk_says(subject)
                self.assertEqual([], pullrequest.reconcile(self.ctx()))
                self.assertEqual(pullrequest.OPEN, self.state_of("feat/x"))

    def test_a_record_whose_number_was_never_learned_is_left_to_the_other_tests(self):
        pullrequest.opened(self.ctx(), "feat/x", "main", "s1")
        self.trunk_says("Add the thing (#41)")

        self.assertEqual([], pullrequest.reconcile(self.ctx()))

    def test_a_trunk_never_fetched_settles_nothing(self):
        pullrequest.opened(self.ctx(), "feat/x", "main", "s1", number=41)

        self.assertEqual([], pullrequest.reconcile(self.ctx()))


class TestThePullRequestLogIsCutDown(LogCase):
    def test_it_keeps_one_row_per_branch_and_reads_the_same(self):
        ctx = self.ctx()
        for number, branch in enumerate(("feat/x", "feat/y", "feat/z"), start=40):
            pullrequest.opened(ctx, branch, "main", "s1")
            pullrequest.learn_number(ctx, branch, number)
        pullrequest.settle(ctx, "feat/y", pullrequest.MERGED)
        before = pullrequest._records(ctx)

        self.assertEqual(4, pullrequest.compact(ctx))
        self.assertEqual(3, len(self.rows(pullrequest.PR_FILE)))
        self.assertEqual(before, pullrequest._records(ctx))

    def test_an_open_record_past_every_obligation_goes_and_hides_no_new_one(self):
        ctx = self.ctx()
        pullrequest._write(ctx, {"branch": "feat/x", "base": "main", "number": 7, "state": "open",
                                 "opened_at": time.time() - 40 * DAY})
        pullrequest.opened(ctx, "feat/x", "main", "s1", number=48)

        self.assertEqual([48], [row["number"] for row in pullrequest.outstanding(ctx)])
        pullrequest.compact(ctx)
        self.assertEqual([48], [row["number"] for row in self.rows(pullrequest.PR_FILE)])

    def test_a_settlement_is_kept_three_months(self):
        ctx = self.ctx()
        for branch, days in (("feat/recent", 89), ("feat/old", 91)):
            pullrequest._write(ctx, {"branch": branch, "base": "main", "number": 7,
                                     "state": "merged", "opened_at": time.time() - 120 * DAY,
                                     "settled_at": time.time() - days * DAY})
        pullrequest.compact(ctx)

        self.assertEqual({"feat/recent"}, set(pullrequest._records(ctx)))

    def test_a_log_under_its_size_is_left_as_it_is(self):
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/x", "main", "s1")
        pullrequest.learn_number(ctx, "feat/x", 41)

        self.assertEqual(0, pullrequest.compact(ctx, pullrequest.COMPACT_BYTES))
        self.assertEqual(2, len(self.rows(pullrequest.PR_FILE)))

    def test_a_write_past_the_size_cuts_it(self):
        ctx = self.ctx()
        with mock.patch.object(pullrequest, "COMPACT_BYTES", 400):
            for number in range(1, 30):
                pullrequest.opened(ctx, "feat/x", "main", "s1")
                pullrequest.learn_number(ctx, "feat/x", number)
                pullrequest.settle(ctx, "feat/x", pullrequest.CLOSED)
        self.assertLess(len(self.rows(pullrequest.PR_FILE)), 4)
        self.assertEqual(29, pullrequest._records(ctx)["feat/x"]["number"])


class TestTheOpenItemsAreCutDown(LogCase):
    def test_an_item_about_a_branch_that_is_gone_is_not_shown(self):
        ctx = self.ctx()
        board.add_open_item(ctx, "a-1", "a finding on the trunk", "main", "s1")
        board.add_open_item(ctx, "b-1", "a finding on a deleted branch", "feat/gone", "s1")

        self.assertEqual(["a-1"], [item["id"] for item in board.open_items(ctx)])
        self.assertEqual(["b-1"], [item["id"] for item in board.open_items(ctx, "feat/gone")])

    def test_the_log_keeps_only_what_it_can_still_show(self):
        ctx = self.ctx()
        board.add_open_item(ctx, "a-1", "shown", "main", "s1")
        board.add_open_item(ctx, "b-1", "closed since", "main", "s1")
        board.close_open_item(ctx, "b-1")
        board.add_open_item(ctx, "c-1", "about a branch that is gone", "feat/gone", "s1")
        store.append_jsonl(store.tier_b(ctx, board.OPEN_ITEMS_FILE),
                           {"id": "d-1", "text": "weeks old", "branch": "main",
                            "created_at": time.time() - 20 * DAY,
                            "last_seen_at": time.time() - 20 * DAY})

        self.assertEqual(4, board.compact_items(ctx))
        self.assertEqual(["a-1"], [row["id"] for row in self.rows(board.OPEN_ITEMS_FILE)])

    def test_git_not_answering_hides_nothing(self):
        ctx = self.ctx()
        board.add_open_item(ctx, "c-1", "about a branch that is gone", "feat/gone", "s1")
        with mock.patch.object(board, "known_branches", return_value=None):
            self.assertEqual(["c-1"], [item["id"] for item in board.open_items(ctx)])
            self.assertEqual(0, board.compact_items(ctx))


class TestALockedAppendWaitsForACompaction(LogCase):
    def test_it_is_written_once_the_lock_is_let_go(self):
        path = store.tier_b(self.ctx(), "some.jsonl")
        with store.file_lock(path.with_suffix(".jsonl.lock")):
            writer = threading.Thread(target=store.append_jsonl, args=(path, {"a": 1}),
                                      kwargs={"locked": True})
            writer.start()
            time.sleep(0.3)
            self.assertEqual([], store.read_jsonl(path))
        writer.join(5)
        self.assertEqual([{"a": 1}], store.read_jsonl(path))


class TestRecordsOfBranchesThatAreGone(LogCase):
    def a_record(self, directory: str, name: str) -> None:
        store.write_json(store.tier_b(self.ctx(), directory, f"{name}.json"), {"command": ["x"]})

    def test_the_green_and_run_records_of_a_gone_branch_go(self):
        git(["update-ref", "refs/remotes/origin/feat/pushed", "HEAD"], self.repo)
        for directory in ("green", "runs"):
            for name in ("main", "feat-x", "feat-pushed", "feat-gone"):
                self.a_record(directory, name)

        self.assertEqual(2, evidence.forget_gone_branches(self.ctx()))
        for directory in ("green", "runs"):
            kept = sorted(path.stem for path in store.tier_b(self.ctx(), directory).glob("*.json"))
            self.assertEqual(["feat-pushed", "feat-x", "main"], kept)

    def test_git_not_answering_drops_nothing(self):
        self.a_record("green", "feat-gone")
        with mock.patch("claude_bestpractice.gitctx.known_branches", return_value=None):
            self.assertEqual(0, evidence.forget_gone_branches(self.ctx()))


class TestAskingForOneStateReadsOnlyIt(LogCase):
    def test_what_a_session_holds_is_answered_without_reading_finished_cards(self):
        ctx = self.ctx()
        for number in range(1, 6):
            self.write(f".claude/claude-bestpractice/plan/done/{number:04d}-finished.md",
                       "---\ntitle: finished\nowner: s1\n---\n")
        self.write(".claude/claude-bestpractice/plan/doing/0006-in-flight.md",
                   "---\ntitle: in flight\nowner: s1\n---\n")
        with mock.patch.object(plan, "_load", wraps=plan._load) as read:
            held = plan.load_all(ctx, plan.DOING)

        self.assertEqual(["0006"], [task.id for task in held])
        self.assertEqual(1, read.call_count)
        self.assertEqual(6, len(plan.load_all(ctx)))


class TestANumberNoHookHeard(LogCase):
    """`gh pr create` behind a `git push` whose hook runs the suite goes to the background, and
    the result the hook reads carries no URL: twelve of twenty-two of one clone's pull requests
    without a number were opened that way."""

    MINE = {"number": 881, "baseRefName": "main", "isDraft": False, "url": "u"}

    def setUp(self) -> None:
        super().setUp()
        stubs = self.tmp / "gh-stub"
        stubs.mkdir()
        self.asked_log = self.tmp / "gh-asked.log"
        (stubs / "gh").write_text('#!/bin/sh\necho "$*" >> "$GH_ASKED_LOG"\n'
                                  "printf '%s' \"$FAKE_GH_LIST\"\n")
        (stubs / "gh").chmod(0o755)
        self.stubs = stubs

    def github_has(self, listed: list) -> None:
        patched = mock.patch.dict(os.environ, {
            "PATH": f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}",
            "GH_ASKED_LOG": str(self.asked_log), "FAKE_GH_LIST": json.dumps(listed)})
        patched.start()
        self.addCleanup(patched.stop)

    def asked(self) -> int:
        return len(self.asked_log.read_text().splitlines()) if self.asked_log.exists() else 0

    def test_it_is_asked_of_github_once_and_learned(self):
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/x", "main", "s1")
        self.github_has([self.MINE])

        self.assertEqual(881, pullrequest.learn_missing_number(ctx, "feat/x"))
        self.assertEqual(881, pullrequest._records(ctx)["feat/x"]["number"])
        self.assertEqual(0, pullrequest.learn_missing_number(ctx, "feat/x"))
        self.assertEqual(1, self.asked())

    def test_a_number_already_heard_asks_nothing(self):
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/x", "main", "s1", number=41)
        self.github_has([self.MINE])

        self.assertEqual(0, pullrequest.learn_missing_number(ctx, "feat/x"))
        self.assertEqual(0, self.asked())

    def test_one_github_never_saw_is_asked_about_for_half_an_hour_and_then_left(self):
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/x", "main", "s1")
        self.github_has([])
        self.assertEqual(0, pullrequest.learn_missing_number(ctx, "feat/x"))
        self.assertEqual(0, pullrequest.learn_missing_number(ctx, "feat/x"))
        self.assertEqual(2, self.asked())

        later = time.time() + pullrequest.NUMBER_ASKED_FOR_SECONDS + 60
        with mock.patch.object(pullrequest.time, "time", return_value=later):
            pullrequest.learn_missing_number(ctx, "feat/x")
            pullrequest.learn_missing_number(ctx, "feat/x")
        self.assertEqual(3, self.asked())

    def test_one_against_another_base_is_not_this_one(self):
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/x", "main", "s1")
        self.github_has([{**self.MINE, "baseRefName": "release"}])

        self.assertEqual(0, pullrequest.learn_missing_number(ctx, "feat/x"))


class TestTheRepair(LogCase):
    def test_it_settles_and_cuts_at_once(self):
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/x", "main", "s1")
        pullrequest.learn_number(ctx, "feat/x", 41)
        self.trunk_says("Add the thing (#41)")
        store.write_json(store.tier_b(ctx, "green", "feat-gone.json"), {"command": ["x"]})

        said = migrate._cut_the_logs_down_to_what_is_live(ctx)

        self.assertIn("settled 1 pull request(s)", said)
        self.assertIn("1 record(s) of branches that are gone", said)
        self.assertEqual(1, len(self.rows(pullrequest.PR_FILE)))
        self.assertEqual("", migrate._cut_the_logs_down_to_what_is_live(self.ctx()))

    def test_it_is_a_registered_repair(self):
        self.assertIn("0043-cut-the-logs-down-to-what-is-live", migrate._REPAIRS)
