"""Merge state, pull requests, and the one view that faces the founder rather than the agent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase, git


class DeliveryCase(RepoCase):
    def ship(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-ship"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )

    def conflict(self) -> None:
        """Leave the repository mid-merge with one file unresolved."""
        self.write("f.txt", "line\n")
        self.commit()
        git(["switch", "-qc", "feat/a"], self.repo)
        self.write("f.txt", "from a\n")
        self.commit()
        git(["switch", "-q", "main"], self.repo)
        self.write("f.txt", "from main\n")
        self.commit()
        subprocess.run(["git", "merge", "feat/a"], cwd=str(self.repo), capture_output=True, timeout=60)


class TestMergeState(DeliveryCase):
    def test_a_clean_repository_reports_nothing(self):
        from claude_bestpractice import delivery

        state = delivery.merge_state(self.ctx())
        self.assertFalse(state.in_progress)
        self.assertEqual(state.render(), "")

    def test_an_unresolved_merge_is_detected_and_named(self):
        from claude_bestpractice import delivery

        self.conflict()
        state = delivery.merge_state(self.ctx())
        self.assertTrue(state.in_progress)
        self.assertEqual(state.kind, "merge")
        self.assertIn("f.txt", state.conflicted)

    def test_the_advice_names_both_ways_out(self):
        """Resolving and abandoning are both legitimate; only guessing is not."""
        from claude_bestpractice import delivery

        self.conflict()
        rendered = delivery.merge_state(self.ctx()).render()
        self.assertIn("git add", rendered)
        self.assertIn("--abort", rendered)
        self.assertIn("never by deleting one side blind", rendered)

    def test_the_board_carries_it_into_the_session(self):
        self.conflict()
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("UNRESOLVED MERGE", body)

    def stop_a_rebase(self) -> None:
        """Leave the rebase halted on a conflict in f.txt."""
        self.write("f.txt", "base\n")
        self.commit()
        git(["switch", "-qc", "topic"], self.repo)
        self.write("f.txt", "from topic\n")
        self.commit()
        git(["switch", "-q", "main"], self.repo)
        self.write("f.txt", "from main\n")
        self.commit()
        git(["switch", "-q", "topic"], self.repo)
        subprocess.run(["git", "rebase", "main"], cwd=str(self.repo), capture_output=True, timeout=60)

    def rebase_through_a_conflict(self) -> None:
        """Rebase, hit a conflict, resolve it, continue — the ordinary recovery."""
        self.stop_a_rebase()
        self.write("f.txt", "resolved\n")
        git(["add", "f.txt"], self.repo)
        subprocess.run(
            ["git", "rebase", "--continue"],
            cwd=str(self.repo), capture_output=True, timeout=60,
            env={**os.environ, "GIT_EDITOR": "true"},
        )

    def test_a_finished_rebase_is_not_reported_as_unfinished(self):
        """Git leaves REBASE_HEAD behind after --continue. It is not a marker.

        Reading it as one made the most ordinary recovery in this workflow poison the
        repository permanently: ship refused, every pull request blocked, every session
        opened with UNRESOLVED REBASE, and nothing on earth cleared it.
        """
        from claude_bestpractice import delivery

        self.rebase_through_a_conflict()
        self.assertTrue(
            (self.repo / ".git" / "REBASE_HEAD").exists(),
            "the leftover this test exists for is not present — git changed, re-verify",
        )
        state = delivery.merge_state(self.ctx())
        self.assertFalse(state.in_progress, f"clean tree reported as mid-{state.kind}")
        blockers = delivery.ready(self.ctx(), "main")
        self.assertNotIn("a rebase is unfinished", blockers)

    def test_a_stopped_rebase_is_still_caught(self):
        """Removing the false positive must not remove the true one."""
        from claude_bestpractice import delivery

        self.stop_a_rebase()
        state = delivery.merge_state(self.ctx())
        self.assertTrue(state.in_progress)
        self.assertEqual(state.kind, "rebase")
        self.assertIn("f.txt", state.conflicted)


class TestWhatShipped(DeliveryCase):
    """The founder looks at outcomes. Every other surface here faces the agent."""

    def test_it_reports_delivered_work_not_files(self):
        from claude_bestpractice import delivery, plan

        task = plan.add(self.ctx(), "Add CSV export to the billing page")
        plan.claim(self.ctx(), task.id, "s1", "main")
        plan.complete(self.ctx(), task.id)

        rendered = delivery.shipped(self.ctx(), "main")
        self.assertIn("Add CSV export", rendered)
        self.assertIn("DELIVERED", rendered)

    def test_it_reports_what_was_ruled_out(self):
        from claude_bestpractice import attempts, delivery

        attempts.record(self.ctx(), "server-side PDF", "headless chrome ate 900MB", ["a.py"])
        self.assertIn("RULED OUT BY TRYING", delivery.shipped(self.ctx(), "main"))

    def test_a_red_suite_is_the_headline_not_a_footnote(self):
        from claude_bestpractice import delivery, evidence

        evidence.record_red(self.ctx(), ["pytest"], "1 failed")
        self.assertIn("RED SUITE", delivery.shipped(self.ctx(), "main"))

    def test_the_diff_is_one_line_at_the_bottom(self):
        from claude_bestpractice import delivery

        self.write("a.py", "x = 1\n")
        self.commit()
        git(["switch", "-qc", "feat/x"], self.repo)
        self.write("a.py", "x = 2\n")
        self.commit()
        rendered = delivery.shipped(self.ctx(), "main")
        self.assertIn("if you want it", rendered)
        self.assertNotIn("+++", rendered, "a diff must never reach this view")

    def test_the_cli_runs(self):
        proc = self.ship()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_cli_refuses_mid_merge(self):
        self.conflict()
        proc = self.ship()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("UNRESOLVED", proc.stderr)


class TestPullRequestReadiness(DeliveryCase):
    def test_an_unfinished_merge_blocks_it(self):
        from claude_bestpractice import delivery

        self.conflict()
        self.assertIn("a merge is unfinished", delivery.ready(self.ctx(), "main"))

    def test_a_red_suite_blocks_it(self):
        from claude_bestpractice import delivery, evidence

        evidence.record_red(self.ctx(), ["pytest"], "1 failed")
        self.assertIn("the test suite is red", delivery.ready(self.ctx(), "main"))

    def test_uncommitted_changes_block_it(self):
        from claude_bestpractice import delivery

        self.write("dirty.py", "x = 1\n")
        self.assertIn("there are uncommitted changes", delivery.ready(self.ctx(), "main"))

    def test_no_commits_blocks_it(self):
        from claude_bestpractice import delivery

        self.assertIn("no commits on top of main", delivery.ready(self.ctx(), "main"))

    def unverified_on(self, branch: str) -> None:
        from claude_bestpractice import store

        store.append_jsonl(
            store.tier_b(self.ctx(), "unverified.jsonl"),
            {"session_id": "s1", "branch": branch, "reason": "no runner", "recorded_at": 0},
        )

    def test_an_unverified_finish_blocks_the_branch_it_happened_on(self):
        from claude_bestpractice import delivery

        self.unverified_on("main")
        self.assertIn("this branch carries an UNVERIFIED finish", delivery.ready(self.ctx(), "main"))

    def test_it_does_not_block_every_other_branch_on_the_clone(self):
        """Tier B is shared by every worktree; the marker is not.

        One session finishing without proof on one branch used to block pull requests on
        every branch of the clone, forever, while the message claimed it was about "this
        branch".
        """
        from claude_bestpractice import delivery

        self.unverified_on("feat/somewhere-else")
        self.assertNotIn(
            "this branch carries an UNVERIFIED finish", delivery.ready(self.ctx(), "main")
        )

    def test_the_body_is_written_for_someone_who_does_not_read_diffs(self):
        from claude_bestpractice import delivery, plan

        task = plan.add(self.ctx(), "Add CSV export to the billing page")
        plan.claim(self.ctx(), task.id, "s1", "main")
        plan.complete(self.ctx(), task.id)

        body = delivery.pr_body(self.ctx(), "main")
        self.assertIn("## What this does", body)
        self.assertIn("Add CSV export", body)
        self.assertNotIn("diff --git", body)


if __name__ == "__main__":
    unittest.main()
