"""A pull request is an obligation — merged, or handed to the founder with reasons."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase, git

from claude_bestpractice import board, evidence, pullrequest, store


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
        board.add_open_item(
            self.ctx(), item_id="review-abcd-1", text="1 review finding(s): secret in src/app.py",
            branch=self.ctx().branch, session_id="s1", subject_paths=["src/app.py"],
        )
        proc = self.merge()
        self.assertEqual("deny", self.decision(proc))
        self.assertIn("secret in src/app.py", self.reason(proc))

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
