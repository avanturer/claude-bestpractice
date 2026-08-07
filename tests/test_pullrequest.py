"""A pull request is an obligation — merged, or handed to the founder with reasons."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase, git

from claude_bestpractice import board, evidence, pullrequest, store

# Code that genuinely trips `sql-interpolation`. Findings are re-asked of the file before
# they are counted, so a fixture asserting on a clean file asserts on nothing.
TRIGGER = 'def q(cur, x):\n    cur.execute(f"SELECT {x}")\n' 


class PRCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        # On a branch, not the trunk: `delivery.ready` counts the commits this branch
        # adds over the trunk, and a branch that adds none is not something to merge.
        git(["checkout", "-q", "-b", "feat/x"], self.repo)

    def gate(self, name: str, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / name)],
            input=json.dumps({"cwd": str(self.repo), **event}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )

    def start(self, session_id: str = "s1") -> None:
        self.gate("session-start", {"session_id": session_id, "hook_event_name": "SessionStart"})

    def tool(self, name: str, tool_input: dict, session_id: str = "s1"):
        return self.gate("pre-tool", {
            "session_id": session_id, "hook_event_name": "PreToolUse",
            "tool_name": name, "tool_input": tool_input,
        })

    def decision(self, proc) -> str | None:
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return None
        return payload.get("hookSpecificOutput", {}).get("permissionDecision")

    def reason(self, proc) -> str:
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return ""
        return payload.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    def open_a_pr(self, session_id: str = "s1"):
        return self.tool(
            "mcp__github__create_pull_request",
            {"owner": "o", "repo": "r", "title": "t", "head": "feat/x", "base": "main"},
            session_id,
        )


class TestOpeningRecordsAnObligation(PRCase):
    def test_the_structured_tool_is_recorded(self):
        self.start()
        self.assertNotEqual("deny", self.decision(self.open_a_pr()))
        self.assertEqual(1, len(pullrequest.outstanding(self.ctx())))

    def test_the_shell_spelling_is_recorded_too(self):
        """A gate that watches only the structured tool is one `gh` walks straight past."""
        self.start()
        self.tool("Bash", {"command": "gh pr create --fill --draft"})
        self.assertEqual(1, len(pullrequest.outstanding(self.ctx())))

    def test_opening_twice_is_still_one_obligation(self):
        self.start()
        self.open_a_pr()
        self.open_a_pr()
        self.assertEqual(1, len(pullrequest.outstanding(self.ctx())))

    def test_an_ordinary_call_records_nothing(self):
        self.start()
        self.tool("Bash", {"command": "gh pr view 12"})
        self.assertEqual([], pullrequest.outstanding(self.ctx()))


class TestTheMergeIsJudged(PRCase):
    """The one point where every check this plugin has can still be applied.

    "If it is all fine, it merges itself" is only safe because the other half is real:
    when the final check finds something, the merge is refused rather than negotiated.
    """

    def green(self) -> None:
        """Put the branch in the state a mergeable branch is actually in."""
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])

    def merge(self, session_id: str = "s1"):
        return self.tool(
            "mcp__github__merge_pull_request",
            {"owner": "o", "repo": "r", "pullNumber": 48}, session_id,
        )

    def test_a_ready_branch_merges_with_no_approval_step(self):
        self.green()
        self.start()
        self.open_a_pr()
        self.assertNotEqual("deny", self.decision(self.merge()))
        self.assertEqual([], pullrequest.outstanding(self.ctx()), "merging left the obligation open")

    def test_a_red_suite_refuses_the_merge(self):
        self.green()
        self.start()
        self.open_a_pr()
        evidence.record_red(self.ctx(), ["pytest"], "2 failed")

        proc = self.merge()
        self.assertEqual("deny", self.decision(proc))
        self.assertIn("red", self.reason(proc))

    def test_uncommitted_work_refuses_the_merge(self):
        self.green()
        self.start()
        self.open_a_pr()
        self.write("src/app.py", "x = 2  # not committed\n")

        proc = self.merge()
        self.assertEqual("deny", self.decision(proc))
        self.assertIn("uncommitted", self.reason(proc))

    def test_a_review_finding_refuses_the_merge(self):
        self.green()
        self.start()
        self.open_a_pr()
        # Real interpolation, because a finding is now re-asked of the file before it is
        # counted: a fixture with a clean file describes a finding that no longer exists.
        self.write("src/app.py", TRIGGER)
        self.commit("interpolate")
        board.add_open_item(
            self.ctx(), item_id="review-abcd-1",
            text="1 review finding(s): sql-interpolation in src/app.py",
            branch=self.ctx().branch, session_id="s1", subject_paths=["src/app.py"],
        )
        proc = self.merge()
        self.assertEqual("deny", self.decision(proc))
        self.assertIn("sql-interpolation in src/app.py", self.reason(proc))

    def test_the_refusal_forbids_fixing_it_into_green(self):
        """The whole point of refusing rather than fixing.

        A model told to get a branch mergeable will get it mergeable, and the moves
        available at merge time — weaken the assertion, widen the tolerance, revert what
        surfaced the problem — all satisfy the letter while making the decision that
        belongs to the person who has to live with it.
        """
        self.green()
        self.start()
        self.open_a_pr()
        evidence.record_red(self.ctx(), ["pytest"], "1 failed")

        reason = self.reason(self.merge())
        self.assertIn("Tell the founder", reason)
        self.assertIn("do NOT push changes", reason)

    def test_the_shell_spelling_is_judged_too(self):
        self.green()
        self.start()
        self.open_a_pr()
        evidence.record_red(self.ctx(), ["pytest"], "1 failed")
        self.assertEqual("deny", self.decision(self.tool("Bash", {"command": "gh pr merge --squash"})))

    def test_a_merge_with_no_obligation_here_is_not_judged(self):
        """Refusing somebody else's pull request on the strength of our working tree.

        Every check available in a hook reads the CURRENT tree, so a refusal aimed at a
        branch that is not checked out would carry a reason that is not true of the thing
        refused — a block that is correct in form and wrong in substance, which costs a
        detour and then costs trust.
        """
        self.green()
        self.start()
        evidence.record_red(self.ctx(), ["pytest"], "1 failed")
        self.assertNotEqual("deny", self.decision(self.merge()))


class TestOpeningOneIsNotAQuestion(PRCase):
    """The founder watched the idea, the checks and the commits go by in the chat and was
    then asked, as a formality, whether to open the pull request. The obligation this
    module records says the answer is always yes — so asking is the plugin making the
    founder confirm its own rule."""

    def remote(self, name: str = "o/r") -> None:
        git(["remote", "add", "origin", f"https://github.com/{name}.git"], self.repo)

    def test_opening_one_here_needs_no_permission(self):
        self.remote()
        self.start()
        self.assertEqual("allow", self.decision(self.open_a_pr()))

    def test_the_shell_spelling_too(self):
        self.remote()
        self.start()
        self.assertEqual("allow", self.decision(self.tool("Bash", {"command": "gh pr create --fill"})))

    def test_a_pull_request_on_somebody_elses_repository_is_not_vouched_for(self):
        """The obligation is still recorded — the board should say what this session did.
        What is refused is the SILENCE: naming another repository is outside every
        boundary this plugin publishes, so the permission layer decides."""
        self.remote()
        self.start()
        proc = self.tool("mcp__github__create_pull_request",
                         {"owner": "someone", "repo": "else", "head": "feat/x", "base": "main"})
        self.assertIsNone(self.decision(proc))
        self.assertEqual(1, len(pullrequest.outstanding(self.ctx())))

    def test_the_shell_spelling_aimed_elsewhere_is_not_vouched_for_either(self):
        self.remote()
        self.start()
        proc = self.tool("Bash", {"command": "gh pr create --repo someone/else --fill"})
        self.assertIsNone(self.decision(proc))

    def test_with_no_remote_at_all_a_named_repository_is_not_vouched_for(self):
        self.start()
        proc = self.tool("mcp__github__create_pull_request",
                         {"owner": "o", "repo": "r", "head": "feat/x", "base": "main"})
        self.assertIsNone(self.decision(proc))


class TestMergingWhatThisGateJustClearedIsNotAQuestion(PRCase):
    def remote(self) -> None:
        git(["remote", "add", "origin", "https://github.com/o/r.git"], self.repo)

    def green(self) -> None:
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])

    def merge(self):
        return self.tool("mcp__github__merge_pull_request",
                         {"owner": "o", "repo": "r", "pullNumber": 48})

    def test_a_merge_this_gate_found_nothing_against_needs_no_permission(self):
        self.remote()
        self.green()
        self.start()
        self.open_a_pr()
        self.assertEqual("allow", self.decision(self.merge()))

    def test_a_merge_with_a_blocker_is_refused_rather_than_vouched_for(self):
        """The vouch is read last, so it can never speak for a call this gate refused."""
        self.remote()
        self.start()
        self.open_a_pr()
        self.assertEqual("deny", self.decision(self.merge()))


class TestFinishedWorkWithNoPullRequestIsDemanded(PRCase):
    """The other end of the same failure. The obligation only starts once a pull request
    exists, so a session that committed everything and asked "shall I open one?" ended the
    turn with nothing on record at all — and the founder, who had already asked for the
    work, was left holding the last step as a formality."""

    def stop(self, session_id: str = "s1"):
        return self.gate("evidence-gate", {
            "session_id": session_id, "hook_event_name": "Stop", "stop_hook_active": False,
        })

    def finished(self) -> None:
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])

    def test_a_turn_cannot_end_with_finished_work_and_no_pull_request(self):
        self.finished()
        self.start()
        proc = self.stop()
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("Open it now, and do not ask whether to", proc.stderr)

    def test_it_interrupts_exactly_once(self):
        """A demand that repeats is a wedge. Marked before it is raised, so a session
        that ignores it or dies does not meet it again."""
        self.finished()
        self.start()
        self.assertEqual(2, self.stop().returncode)
        second = self.stop()
        self.assertNotIn("Open it now", second.stderr)

    def test_a_branch_that_is_not_ready_is_not_demanded_of(self):
        """Uncommitted work, a red suite, no commits at all: each is a reason there is
        nothing to open yet, and each was already computed by `delivery.ready`."""
        self.write("src/app.py", "x = 1\n")
        self.start()
        proc = self.stop()
        self.assertNotIn("Open it now", proc.stderr)

    def test_a_branch_that_already_has_one_is_left_alone(self):
        """The obligation machinery owns it from here, and two demands about one branch
        in consecutive turns is the plugin talking to itself."""
        self.finished()
        self.start()
        self.open_a_pr()
        proc = self.stop()
        self.assertNotIn("Open it now", proc.stderr)

    def test_a_branch_whose_pull_request_was_merged_is_not_asked_for_a_second_one(self):
        """A local checkout whose base is behind still counts commits on top of it, so
        asking "is one OPEN" here would demand a pull request for work that has landed."""
        self.finished()
        self.start()
        self.open_a_pr()
        pullrequest.settle(self.ctx(), "feat/x", pullrequest.MERGED)
        proc = self.stop()
        self.assertNotIn("Open it now", proc.stderr)


class TestAPullRequestIsNeverLeftHanging(PRCase):
    """The reported failure: the PR is opened, the turn ends, and nobody comes back."""

    def stop(self, session_id: str = "s1", active: bool = False):
        return self.gate("evidence-gate", {
            "session_id": session_id, "hook_event_name": "Stop", "stop_hook_active": active,
        })

    def test_a_turn_cannot_end_quietly_on_an_open_pull_request(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()

        proc = self.stop()
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("Merge it now", proc.stderr)

    def test_a_blocked_pull_request_is_reported_rather_than_repaired(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()
        evidence.record_red(self.ctx(), ["pytest"], "3 failed")

        proc = self.stop()
        self.assertEqual(2, proc.returncode)
        self.assertIn("Report exactly this to the founder", proc.stderr)
        self.assertIn("Do NOT push changes", proc.stderr)

    def test_it_interrupts_exactly_once(self):
        """A second block on the same pull request would be a wedge, not a reminder.

        The hand-off is written before the block, so ignoring it, crashing, or hitting the
        escalation ceiling all leave the next Stop free. Past the one interruption the
        board is what keeps the pull request from being forgotten.
        """
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()

        self.assertEqual(2, self.stop().returncode)
        self.assertEqual(0, self.stop().returncode, "blocked twice for one pull request")

    def test_after_the_hand_off_it_is_carried_on_the_board(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()
        self.stop()

        ctx = self.ctx()
        texts = [i.get("text", "") for i in board.open_items(ctx, branch=ctx.branch)]
        self.assertTrue(any("PULL REQUEST open" in t for t in texts), texts)

    def test_a_later_session_is_told_about_it(self):
        """The half that makes "never forgotten" true across sessions, not just turns."""
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()

        proc = self.gate("session-start", {"session_id": "later", "hook_event_name": "SessionStart"})
        self.assertIn("OPEN PULL REQUESTS", proc.stdout)

    def test_a_merged_pull_request_stops_being_mentioned(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()
        self.tool("mcp__github__merge_pull_request", {"owner": "o", "repo": "r", "pullNumber": 1})

        self.assertEqual("", pullrequest.line(self.ctx()))
        self.assertEqual(0, self.stop().returncode)


class TestItCanBeTurnedOff(PRCase):
    """A human with root can disable everything here, and should be able to."""

    def test_the_flag_stands_the_whole_thing_down(self):
        self.configure(manage_pull_requests=False)
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()

        self.assertEqual([], pullrequest.outstanding(self.ctx()))
        proc = self.gate("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop"})
        self.assertEqual(0, proc.returncode)


class TestTheLedger(RepoCase):
    def test_an_ancient_obligation_stops_being_asserted(self):
        """A warning nothing can clear is one the founder learns to scroll past."""
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/old", "main", "s1")
        rows = list(store.read_jsonl(store.tier_b(ctx, pullrequest.PR_FILE)))
        rows[0]["opened_at"] = time.time() - (pullrequest.MAX_AGE_SECONDS + 3600)
        store.tier_b(ctx, pullrequest.PR_FILE).write_text("", encoding="utf-8")
        store.append_jsonl(store.tier_b(ctx, pullrequest.PR_FILE), rows[0])
        self.assertEqual([], pullrequest.outstanding(ctx))

    def test_settling_a_branch_leaves_the_others_alone(self):
        ctx = self.ctx()
        pullrequest.opened(ctx, "feat/a", "main", "s1")
        pullrequest.opened(ctx, "feat/b", "main", "s2")
        pullrequest.settle(ctx, "feat/a", pullrequest.MERGED)
        self.assertEqual(["feat/b"], [r["branch"] for r in pullrequest.outstanding(ctx)])


if __name__ == "__main__":
    unittest.main()


class TestAPullRequestThisPluginNeverSaw(PRCase):
    """Opened before the plugin was installed, or from the website.

    There is no obligation on record and no way to discover one without a network call
    this gate must not make. The one case that is still decidable is a merge that names
    the checked-out branch — there the local checks are known to be about the thing being
    merged, so it is judged like any other.
    """

    def test_an_unnamed_shell_merge_is_judged_on_this_branch(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        self.start()
        evidence.record_red(self.ctx(), ["pytest"], "1 failed")
        self.assertEqual("deny", self.decision(self.tool("Bash", {"command": "gh pr merge --squash"})))

    def test_a_numbered_merge_of_an_unknown_pull_request_is_not(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        self.start()
        evidence.record_red(self.ctx(), ["pytest"], "1 failed")
        self.assertNotEqual("deny", self.decision(self.tool("Bash", {"command": "gh pr merge 91"})))


class TestAFindingFromMainIsNotThisPullRequests(PRCase):
    """Issue #69. The workflow REQUIRES `git merge origin/main` before merging, and that
    import carried every open finding in main onto the branch.

    A pull request of eight markdown files was refused over SQL interpolation in a Python
    module it never touched. The longer main got, the more a branch inherited — so syncing
    with main, which the gate itself demands, made going green impossible.
    """

    def branch_work(self) -> None:
        self.write("docs/notes.md", "# notes\n")
        self.write("src/live.py", TRIGGER)
        self.commit("document the thing")
        evidence.record_green(self.ctx(), ["pytest"])

    def finding(self, path: str) -> None:
        board.add_open_item(
            self.ctx(), item_id=f"review-abcd-{path}",
            text=f"1 review finding(s): sql-interpolation in {path}",
            branch=self.ctx().branch, session_id="s1", subject_paths=[path],
        )

    def test_a_finding_in_a_file_the_branch_never_touched_is_not_a_blocker(self):
        git(["checkout", "-q", "main"], self.repo)
        self.write("backend/config.py", "DSN = 'postgres://localhost/dev'\n")
        self.commit("main moves ahead")
        git(["checkout", "-q", "feat/x"], self.repo)
        git(["merge", "-q", "--no-edit", "main"], self.repo)

        self.branch_work()
        self.finding("backend/config.py")
        self.assertEqual([], pullrequest.blockers(self.ctx(), "main"))

    def test_a_finding_in_a_file_the_branch_does_touch_still_blocks(self):
        """The narrowing is the PR's own diff, not an amnesty for review findings."""
        self.branch_work()
        self.finding("src/live.py")
        self.assertIn(
            "1 review finding(s): sql-interpolation in src/live.py",
            pullrequest.blockers(self.ctx(), "main"),
        )


class TestAGreenRunIsObservedAcrossTheClone(PRCase):
    """Issue #69, second half: the suite had run, in the branch's own worktree, and the
    gate still said "no test run has ever been observed on this branch".

    The record sat in the worktree's own Tier A, so it was invisible from anywhere else in
    the same clone — and it carried no branch test at all, so a run on one branch answered
    for every other.
    """

    def test_a_run_on_another_branch_does_not_answer_for_this_one(self):
        evidence.record_green(self.ctx(), ["pytest"])
        self.assertIsNotNone(evidence.last_green(self.ctx()))

        git(["checkout", "-q", "-b", "feat/other"], self.repo)
        self.assertIsNone(evidence.last_green(self.ctx()), "a run on feat/x answered for feat/other")

    def test_the_record_is_shared_by_every_worktree_of_the_clone(self):
        """A merge decided from one tree has to see a run observed in another."""
        evidence.record_green(self.ctx(), ["pytest"])
        sibling = self.tmp / "sibling"
        # `--force`: git refuses one branch in two trees, and one branch in two trees is
        # precisely the arrangement being tested — a run observed in the tree the work
        # happened in, read from the tree the merge is decided in.
        git(["worktree", "add", "-q", "--force", str(sibling), "feat/x"], self.repo)

        from claude_bestpractice.gitctx import resolve

        self.assertIsNotNone(evidence.last_green(resolve(sibling)))


class TestTheMergeIsJudgedOnThePullRequest(PRCase):
    """Issue #74. A merge is not a write to a working tree, and a session in a main
    checkout is the normal case for anything that coordinates work — reading pull
    requests, merging, releasing. Judging the occupied tree refused every one of them,
    and each reason named the wrong subject.
    """

    def test_commits_are_counted_on_the_head_not_the_session_tree(self):
        self.write("src/app.py", "x = 1\n")
        self.commit("real work on the branch")
        evidence.record_green(self.ctx(), ["pytest"])
        head = self.ctx().branch

        git(["checkout", "-q", "main"], self.repo)
        problems = pullrequest.blockers(self.ctx(), "main", head)
        self.assertNotIn(f"no commits on {head} over main", problems)
        self.assertEqual([], problems, "a main-checkout session could not merge a ready branch")

    def test_another_branchs_unverified_finish_is_not_this_pull_requests(self):
        """It belonged to a different session, on a different task, hours earlier."""
        self.write("src/app.py", "x = 1\n")
        self.commit("real work")
        evidence.record_green(self.ctx(), ["pytest"])
        head = self.ctx().branch

        store.append_jsonl(
            store.tier_b(self.ctx(), "unverified.jsonl"),
            {"branch": "main", "at": time.time(), "why": "somebody else's task"},
        )
        git(["checkout", "-q", "main"], self.repo)
        self.assertEqual([], pullrequest.blockers(self.ctx(), "main", head))

    def test_the_head_branch_still_has_to_be_green(self):
        """Scoping to the pull request is not an amnesty for the pull request."""
        self.write("src/app.py", "x = 1\n")
        self.commit("real work")
        head = self.ctx().branch
        git(["checkout", "-q", "main"], self.repo)

        self.assertIn(
            f"no test run has ever been observed on {head}",
            pullrequest.blockers(self.ctx(), "main", head),
        )


class TestAFalseFindingCanBeRuledOut(PRCase):
    """Issue #75. Two false findings blocked the merge permanently: nothing to fix, so the
    list could never empty, and the only exits were rewriting correct code or switching the
    gate off."""

    def finding(self) -> None:
        board.add_open_item(
            self.ctx(), item_id="review-abcd-1",
            text="1 review finding(s): sql-interpolation in src/app.py",
            branch=self.ctx().branch, session_id="s1", subject_paths=["src/app.py"],
        )

    def test_a_dismissed_finding_stops_blocking(self):
        self.write("src/app.py", TRIGGER)
        self.commit("work")
        evidence.record_green(self.ctx(), ["pytest"])
        self.finding()
        self.assertTrue(pullrequest.blockers(self.ctx(), "main"))

        board.dismiss(self.ctx(), "sql-interpolation", "src/app.py")
        self.assertEqual([], pullrequest.blockers(self.ctx(), "main"))

    def test_dismissing_one_file_does_not_clear_another(self):
        self.write("src/app.py", TRIGGER)
        self.commit("work")
        evidence.record_green(self.ctx(), ["pytest"])
        self.finding()

        board.dismiss(self.ctx(), "sql-interpolation", "src/other.py")
        self.assertTrue(pullrequest.blockers(self.ctx(), "main"))
