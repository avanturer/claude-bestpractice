"""Issue #325: a pull request GitHub opened on the name its branch was pushed as was filed under
the local name, and settled as closed once the local branch was deleted.

`git push -u origin tmp/assistant-all:fix/diet-hidden-animals`, then `gh pr create --fill`: GitHub
opened #1135 on `fix/diet-hidden-animals`, and the record said `tmp/assistant-all`. The temporary
branch was merged locally and deleted, `reconcile` read the branch as gone and the pull request as
closed, and the founder's `+merge` answering a message that named #1132 and #1135 covered #1132
alone: `gh pr merge 1135` was refused as "recorded here as closed on tmp/assistant-all", twice.
"""

from __future__ import annotations

import json
import os
import time
from unittest import mock

from helpers import add_origin, git
from test_two_pull_requests_on_one_word import LINK, TwoPullRequestsCase

from claude_bestpractice import board, delivery, migrate, pullrequest, store

LOCAL, HEAD = "tmp/assistant-all", "fix/diet-hidden-animals"


class PushedCase(TwoPullRequestsCase):
    def setUp(self) -> None:
        super().setUp()
        git(["remote", "remove", "origin"], self.repo)
        git(["checkout", "-q", "main"], self.repo)
        add_origin(self.repo, self.tmp)
        self.start()

    def pushed_as(self, head: str = HEAD, local: str = LOCAL) -> None:
        """A branch of this clone pushed under another name, tracking it."""
        git(["checkout", "-q", "-b", local], self.repo)
        self.committed("y = 1\n")
        git(["push", "-q", "-u", "origin", f"{local}:{head}"], self.repo)

    def opened_1135(self) -> None:
        self.pushed_as()
        self.opened_in_a_shell("gh pr create --fill", 1135)

    def deleted(self, branch: str = LOCAL) -> None:
        git(["checkout", "-q", "main"], self.repo)
        git(["branch", "-D", branch], self.repo)

    def the_report(self) -> None:
        """#1135 opened from the temporary branch, which goes; #1132 opened beside it, and a
        message naming both answered with `+merge`."""
        self.opened_1135()
        self.deleted()
        pullrequest.reconcile(self.ctx())
        git(["checkout", "-q", "feat/x"], self.repo)
        self.committed("z = 1\n")
        self.opened_in_a_shell("gh pr create --fill", 1132)
        self.stop("Ready: #1132 and #1135. Say +merge.")
        self.accept()


class TestItIsFiledUnderTheHeadGitHubOpenedItOn(PushedCase):
    def test_under_the_name_it_was_pushed_as_with_the_local_branch_beside_it(self):
        self.opened_1135()
        record = self.record(HEAD)
        self.assertEqual((1135, pullrequest.OPEN, LOCAL),
                         (record["number"], record["state"], record[pullrequest.LOCAL]))
        self.assertNotIn(LOCAL, pullrequest._records(self.ctx()))

    def test_a_base_the_call_names_changes_nothing_of_where_it_was_pushed(self):
        self.pushed_as()
        self.assertEqual((HEAD, LOCAL), pullrequest.opening(
            self.ctx(), "Bash", "gh pr create --fill --base main", {}, str(self.repo)))

    def test_a_head_the_call_names_is_taken_as_written(self):
        self.pushed_as()
        for command in ("gh pr create --head other/name --fill", "gh pr create -H other/name"):
            with self.subTest(command=command):
                self.assertEqual(("other/name", ""), pullrequest.opening(
                    self.ctx(), "Bash", command, {}, str(self.repo)))

    def test_one_only_its_result_reached_is_filed_the_same_way(self):
        """The PostToolUse files it where PreToolUse did not, as for the structured tool."""
        self.pushed_as()
        self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --fill"},
            "tool_response": {"stdout": f"{LINK.format(1135)}\n", "stderr": ""}})
        self.assertEqual(LOCAL, self.record(HEAD)[pullrequest.LOCAL])

    def test_a_branch_pushed_under_its_own_name_has_no_other(self):
        self.pushed_as(head=LOCAL)
        self.opened_in_a_shell("gh pr create --fill", 1135)
        self.assertNotIn(pullrequest.LOCAL, self.record(LOCAL))

    def test_where_it_is_pushed_comes_before_what_it_tracks(self):
        """`gh` opens it where `@{push}` says, when that resolves: here the remote branch of its
        own name, level with it, though it tracks another."""
        self.pushed_as()
        git(["push", "-q", "origin", LOCAL], self.repo)
        git(["config", "push.default", "current"], self.repo)
        self.assertEqual((LOCAL, ""), pullrequest.opening(
            self.ctx(), "Bash", "gh pr create --fill", {}, str(self.repo)))

    def test_one_ahead_of_the_branch_it_was_pushed_as_is_filed_under_its_own(self):
        """`gh` pushes it before it opens one: the remote copy is not the commit proposed."""
        self.pushed_as()
        self.committed("y = 2\n")
        self.assertEqual((LOCAL, ""), pullrequest.opening(
            self.ctx(), "Bash", "gh pr create --fill", {}, str(self.repo)))

    def test_a_branch_cut_from_the_trunk_is_never_filed_as_the_trunk(self):
        git(["checkout", "-q", "-b", "feat/new", "origin/main"], self.repo)
        self.assertEqual("", pullrequest._pushed_as(self.ctx(), "feat/new"))
        git(["push", "-q", "origin", "main:release"], self.repo)
        git(["branch", "-q", "--set-upstream-to=origin/release"], self.repo)
        self.assertEqual("", pullrequest._pushed_as(self.ctx(), "feat/new", "release"),
                         "nor as the base it is opened into")
        self.assertEqual("release", pullrequest._pushed_as(self.ctx(), "feat/new"))

    def test_a_branch_named_under_this_one_is_not_this_one(self):
        """`for-each-ref refs/heads/tmp` lists `tmp/…` too."""
        self.pushed_as()
        self.assertEqual("", pullrequest._pushed_as(self.ctx(), "tmp"))


class TestTheTreeOnTheLocalBranchFindsIt(PushedCase):
    def test_the_stop_gate_owes_no_second_pull_request(self):
        self.opened_1135()
        self.assertTrue(pullrequest.known(self.ctx(), LOCAL))
        self.assertIsNone(pullrequest.missing(self.ctx(), "s1"))

    def test_a_merge_from_it_is_judged_as_that_trees(self):
        """Judged on the local branch, which has the commits: under the head's name nothing here
        did, and the merge was refused as having no commits over main."""
        self.opened_1135()
        self.accept()
        proc = self.merge(1135)
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))
        self.assertEqual(pullrequest.MERGED, self.record(HEAD)["state"])

    def test_an_unverified_finish_on_the_local_branch_is_theirs_to_accept(self):
        self.opened_1135()
        store.append_jsonl(store.tier_b(self.ctx(), "unverified.jsonl"), {
            "session_id": "s1", "branch": LOCAL, "head": self.ctx().head,
            "reason": "no suite covers mobile/", "recorded_at": time.time()})
        self.accept()
        refused = self.merge(1135)
        self.assertIn(delivery.UNVERIFIED, self.reason(refused))
        self.accept()
        proc = self.merge(1135)
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def test_the_board_does_not_call_it_a_branch_without_one(self):
        self.opened_1135()
        git(["branch", "feat/orphan", LOCAL], self.repo)
        git(["checkout", "-q", "main"], self.repo)
        with mock.patch("time.time", return_value=time.time() + 7200):
            named = [branch for branch, _ in pullrequest.branches_without_one(self.ctx(), 1)]
        self.assertEqual(["feat/orphan"], [branch for branch in named if branch != "feat/x"])

    def test_opening_it_closes_the_demand_made_on_the_local_branch(self):
        self.pushed_as()
        board.add_open_item(self.ctx(), item_id=f"{pullrequest.MISSING_ITEM}{LOCAL}-1",
                            text=f"NO PULL REQUEST for finished work on {LOCAL}", branch=LOCAL,
                            session_id="s1", subject_paths=[])
        self.opened_in_a_shell("gh pr create --fill", 1135)
        self.assertEqual([], [item for item in board.open_items(self.ctx(), branch=LOCAL,
                                                                limit=None)
                              if "NO PULL REQUEST" in str(item.get("text", ""))])

    def test_github_is_asked_for_its_number_by_the_head(self):
        self.pushed_as()
        self.tool("Bash", {"command": "gh pr create --fill"})
        self.github_has_for(HEAD, 1135)
        self.assertEqual(1135, pullrequest.learn_missing_number(self.ctx(), LOCAL))

    def github_has_for(self, head: str, number: int) -> None:
        """A `gh` that lists the pull request only when asked about `head`."""
        stub = self.stubs / "gh"
        stub.write_text('#!/bin/sh\ncase "$*" in *"--head $WANTED "*) '
                        'printf \'%s\' "$FAKE_GH_LIST";; *) printf "[]";; esac\n')
        patched = mock.patch.dict(os.environ, {
            "PATH": f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}", "WANTED": head,
            "FAKE_GH_LIST": json.dumps([{"number": number, "url": LINK.format(number),
                                         "isDraft": False, "baseRefName": "main",
                                         "createdAt": "2026-11-12T10:00:00Z"}]),
        })
        patched.start()
        self.addCleanup(patched.stop)


class TestEveryQuestionAboutTheLocalBranchFindsItsRecord(PushedCase):
    """The Stop gate, the merge gate and the status line ask by the branch checked out."""

    def setUp(self) -> None:
        super().setUp()
        self.opened_1135()

    def test_the_stop_gate_puts_it_to_the_founder_once(self):
        told = self.stop()
        self.assertIn("pull request #1135 is open", told.stderr)
        self.assertTrue(pullrequest.handed_off(self.ctx(), LOCAL))
        self.assertIsNone(pullrequest.unhanded(self.ctx(), LOCAL))

    def test_what_blocks_it_is_written_on_its_record(self):
        pullrequest.hand_off(self.ctx(), self.record(HEAD), [])
        self.assertTrue(pullrequest.refresh_blockers(self.ctx(), LOCAL, ["a stray file"]))
        self.assertEqual(["a stray file"], self.record(HEAD)["blockers"])

    def test_marking_it_a_draft_and_ready_again(self):
        self.tool("Bash", {"command": "gh pr ready --undo"})
        self.assertTrue(self.record(HEAD)["draft"])
        pullrequest.redraft(self.ctx(), LOCAL, False)
        self.assertFalse(self.record(HEAD)["draft"])

    def test_findings_a_refusal_listed_are_theirs_to_merge_past(self):
        finding = "1 review finding(s): sql-interpolation in src/app.py"
        pullrequest.note_refusal(self.ctx(), LOCAL, self.ctx().head, [finding], "s1")
        self.assertEqual([finding], self.record(HEAD)[pullrequest.SHOWN_FINDINGS])
        pullrequest._accept_its_findings(self.ctx(), LOCAL, time.time() + 1)
        self.assertEqual([], pullrequest._not_merged_past(self.ctx(), LOCAL, [finding]))

    def test_a_merge_that_names_no_number_is_its_merge(self):
        self.accept()
        proc = self.ran("Bash", {"command": "gh pr merge --squash"})
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def test_an_older_record_filed_under_the_local_name_does_not_hide_it(self):
        """Closed, as an older version filed and closed one; the open one is the one asked about."""
        pullrequest.opened(self.ctx(), LOCAL, "main", "s1", number=1100)
        pullrequest.settle(self.ctx(), LOCAL, pullrequest.CLOSED)
        self.assertEqual(1135, pullrequest._record_of(self.ctx(), LOCAL)["number"])

    def test_settling_by_the_name_it_is_filed_under_settles_that_one(self):
        """`gh pr close 1100` finds the record by its number, and settles it by its name."""
        store.append_jsonl(store.tier_b(self.ctx(), pullrequest.PR_FILE), {
            "branch": LOCAL, "base": "main", "number": 1100, "url": "", "session_id": "s1",
            "opened_at": time.time() - 60, "state": pullrequest.OPEN, "handed_off_at": 0.0})
        pullrequest.settle(self.ctx(), LOCAL, pullrequest.CLOSED)
        self.assertEqual((pullrequest.CLOSED, pullrequest.OPEN),
                         (self.record(LOCAL)["state"], self.record(HEAD)["state"]))

    def test_of_two_opened_from_it_the_newest(self):
        git(["push", "-q", "origin", f"{LOCAL}:a/first"], self.repo)
        pullrequest.opened(self.ctx(), "a/first", "main", "s1", number=1199, local=LOCAL)
        self.assertEqual(1199, pullrequest._record_of(self.ctx(), LOCAL)["number"])


class TestALocalBranchGoneSettlesNothingGitHubHasOpen(PushedCase):
    def test_the_report(self):
        self.the_report()
        self.merged(1135)
        self.merged(1132)

    def test_deleting_the_local_branch_leaves_it_open(self):
        self.opened_1135()
        self.deleted()
        self.assertEqual([], pullrequest.reconcile(self.ctx()))
        self.assertEqual(pullrequest.OPEN, self.record(HEAD)["state"])

    def test_the_trunk_holding_its_work_settles_it(self):
        self.opened_1135()
        git(["push", "-q", "origin", f"{LOCAL}:main"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)
        self.deleted()
        self.assertEqual([HEAD], pullrequest.reconcile(self.ctx()))
        self.assertEqual(pullrequest.MERGED, self.record(HEAD)["state"])

    def test_the_tree_on_the_local_branch_asks_the_trunk_about_it(self):
        self.opened_1135()
        git(["push", "-q", "origin", f"{LOCAL}:main"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)
        self.assertEqual([HEAD], pullrequest.reconcile(self.ctx(), LOCAL))

    def test_the_local_branch_answers_once_the_remote_copy_is_gone(self):
        self.opened_1135()
        git(["push", "-q", "origin", f"{LOCAL}:main"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)
        git(["update-ref", "-d", f"refs/remotes/origin/{HEAD}"], self.repo)
        self.assertEqual([HEAD], pullrequest.reconcile(self.ctx()))
        self.assertEqual(pullrequest.MERGED, self.record(HEAD)["state"])

    def test_the_remote_copy_gone_leaves_it_open_while_the_local_branch_is_here(self):
        self.opened_1135()
        git(["update-ref", "-d", f"refs/remotes/origin/{HEAD}"], self.repo)
        self.assertEqual([], pullrequest.reconcile(self.ctx()))

    def test_gone_under_both_names_it_leaves_the_board_and_says_so(self):
        self.opened_1135()
        self.deleted()
        git(["update-ref", "-d", f"refs/remotes/origin/{HEAD}"], self.repo)
        self.assertEqual([HEAD], pullrequest.reconcile(self.ctx()))
        record = self.record(HEAD)
        self.assertEqual((pullrequest.CLOSED, pullrequest.GONE),
                         (record["state"], record[pullrequest.SETTLED_BY]))
        self.assertNotIn(1135, pullrequest._settled_numbers(self.ctx()))

    def an_old_record_gone(self) -> None:
        """Filed under the local name, as before this version, and its branch since deleted."""
        git(["checkout", "-q", "-b", LOCAL], self.repo)
        pullrequest.opened(self.ctx(), LOCAL, "main", "s1", number=1135, url=LINK.format(1135))
        self.deleted()

    def test_one_filed_under_a_branch_gone_is_named_by_the_word_and_merges(self):
        self.an_old_record_gone()
        self.assertEqual([LOCAL], pullrequest.reconcile(self.ctx()))
        git(["checkout", "-q", "feat/x"], self.repo)
        self.committed("z = 1\n")
        self.opened_in_a_shell("gh pr create --fill", 1132)
        self.stop("Ready: #1132 and #1135. Say +merge.")
        self.accept()
        self.merged(1135)
        self.assertEqual(pullrequest.MERGED, self.record(LOCAL)["state"],
                         "a merge that ran is GitHub's word")
        self.assertNotIn(pullrequest.SETTLED_BY, self.record(LOCAL))

    def test_a_refusal_puts_it_to_the_founder_and_their_next_word_names_it(self):
        self.an_old_record_gone()
        pullrequest.reconcile(self.ctx())
        git(["checkout", "-q", "feat/x"], self.repo)
        self.committed("z = 1\n")
        self.opened_in_a_shell("gh pr create --fill", 1132)
        self.accept()
        refused = self.merge(1135)
        self.assertEqual("deny", self.decision(refused))
        self.assertIn(f"left the board here when {LOCAL} went from this clone",
                      self.reason(refused))
        self.accept()
        self.merged(1135)

    def test_a_merge_by_number_marks_merged_only_what_had_gone(self):
        """Another tree's pull request merged by its number is spent from the word and left to
        `reconcile`, as the merge gate leaves somebody else's (#135); only one whose branch went
        from this clone is marked, since nothing else here would ever settle it."""
        self.opened_1135()
        git(["checkout", "-q", "feat/x"], self.repo)
        self.accept()
        self.merged(1135)
        self.assertEqual(pullrequest.OPEN, self.record(HEAD)["state"])

    def test_one_closed_on_github_stays_closed(self):
        """Closed by `gh pr close`, which is GitHub's word: no word names it again."""
        self.opened_1135()
        self.tool("Bash", {"command": "gh pr close 1135"})
        self.deleted()
        git(["update-ref", "-d", f"refs/remotes/origin/{HEAD}"], self.repo)
        pullrequest.reconcile(self.ctx())
        self.assertIn(1135, pullrequest._settled_numbers(self.ctx()))
        self.assertEqual([], pullrequest.named_in_message(self.ctx(), "#1135"))


class TestTheRepairMarksWhatAnOlderVersionClosed(PushedCase):
    def step(self) -> str:
        return migrate._REPAIRS["0049-a-branch-gone-closes-no-pull-request"][1](self.ctx())

    def closed(self, branch: str, number: int) -> None:
        """A record the old `reconcile` closed: no mark of why."""
        store.append_jsonl(store.tier_b(self.ctx(), pullrequest.PR_FILE), {
            "branch": branch, "base": "main", "number": number, "url": "", "session_id": "s1",
            "opened_at": time.time() - 3600, "state": pullrequest.CLOSED,
            "settled_at": time.time() - 60, "handed_off_at": 0.0})

    def test_a_closed_record_whose_branch_is_gone_is_marked_once(self):
        self.closed(LOCAL, 1135)
        self.assertIn("1 pull request(s)", self.step())
        self.assertEqual(pullrequest.GONE, self.record(LOCAL)[pullrequest.SETTLED_BY])
        self.assertEqual("", self.step(), "idempotent")
        self.assertEqual([1135], pullrequest.named_in_message(self.ctx(), "#1135"))

    def test_what_it_does_not_mark(self):
        git(["branch", "feat/kept"], self.repo)
        self.closed("feat/kept", 7)
        self.closed("feat/numberless", 0)
        git(["commit", "-q", "--allow-empty", "-m", "Squashed (#9)"], self.repo)
        git(["push", "-q", "origin", "HEAD:main"], self.repo)
        git(["fetch", "-q", "origin"], self.repo)
        self.closed("feat/squashed", 9)
        self.closed("fix/remote-only", 11)
        rows = store.read_jsonl(store.tier_b(self.ctx(), pullrequest.PR_FILE))
        store.append_jsonl(store.tier_b(self.ctx(), pullrequest.PR_FILE),
                           {**rows[-1], pullrequest.LOCAL: "feat/kept"})
        self.assertEqual("", self.step())
        for branch in ("feat/kept", "feat/numberless", "feat/squashed", "fix/remote-only"):
            with self.subTest(branch=branch):
                self.assertNotIn(pullrequest.SETTLED_BY, self.record(branch))
