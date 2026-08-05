"""The work ledger: what is done, in flight, and next — across parallel sessions."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase, git

from claude_bestpractice import plan, sessions, store


class PlanCase(RepoCase):
    def session(self, session_id: str, pid: int | None = None) -> sessions.SessionRecord:
        rec = self.session_record(session_id, pid)
        sessions.register(self.ctx(), rec)
        return rec


class TestLifecycle(PlanCase):
    def test_add_lands_in_next(self):
        task = plan.add(self.ctx(), "Export invoices as CSV")
        self.assertEqual(task.state, plan.NEXT)
        self.assertEqual(task.id, "0001")
        self.assertIn("export-invoices-as-csv", task.path.name)

    def test_state_is_the_directory(self):
        """A transition is a rename, which is why parallel branches merge cleanly."""
        ctx = self.ctx()
        task = plan.add(ctx, "Ship it")
        self.assertTrue(task.path.parent.name == plan.NEXT)

        claimed, _ = plan.claim(ctx, task.id, "s1", "main")
        self.assertEqual(claimed.path.parent.name, plan.DOING)
        self.assertFalse(task.path.exists())

        done, _ = plan.complete(ctx, task.id)
        self.assertEqual(done.path.parent.name, plan.DONE)

    def test_ids_increment(self):
        ctx = self.ctx()
        self.assertEqual(plan.add(ctx, "first").id, "0001")
        self.assertEqual(plan.add(ctx, "second").id, "0002")

    def test_summary_counts_each_state(self):
        ctx = self.ctx()
        a = plan.add(ctx, "one")
        plan.add(ctx, "two")
        plan.claim(ctx, a.id, "s1", "main")
        counts = plan.summary(ctx)
        self.assertEqual((counts["next"], counts["doing"], counts["done"]), (1, 1, 0))

    def test_completing_an_unknown_task_reports_it(self):
        _, error = plan.complete(self.ctx(), "9999")
        self.assertIn("no task", error)


class TestClaiming(PlanCase):
    def test_a_live_session_holds_its_claim(self):
        ctx = self.ctx()
        self.session("alpha")
        task = plan.add(ctx, "shared work")
        plan.claim(ctx, task.id, "alpha", "main")

        _, error = plan.claim(ctx, task.id, "beta", "feature")
        self.assertIn("held by live session", error)

    def test_a_dead_session_claim_is_taken_over(self):
        """Otherwise a crashed session leaves work marked in-flight forever."""
        ctx = self.ctx()
        self.session("ghost", pid=999_999_999)
        task = plan.add(ctx, "orphaned work")
        plan.claim(ctx, task.id, "ghost", "main")

        claimed, error = plan.claim(ctx, task.id, "beta", "feature")
        self.assertEqual(error, "")
        self.assertEqual(claimed.owner, "beta")

    def test_reclaiming_your_own_task_is_allowed(self):
        ctx = self.ctx()
        self.session("alpha")
        task = plan.add(ctx, "mine")
        plan.claim(ctx, task.id, "alpha", "main")
        _, error = plan.claim(ctx, task.id, "alpha", "main")
        self.assertEqual(error, "")

    def test_a_done_task_cannot_be_claimed(self):
        ctx = self.ctx()
        task = plan.add(ctx, "finished")
        plan.complete(ctx, task.id)
        _, error = plan.claim(ctx, task.id, "s1", "main")
        self.assertIn("already done", error)

    def test_reaping_releases_claims(self):
        ctx = self.ctx()
        self.session("ghost", pid=999_999_999)
        task = plan.add(ctx, "orphaned")
        plan.claim(ctx, task.id, "ghost", "main")

        sessions.reap(ctx)
        released = plan.find(ctx, task.id)
        self.assertEqual(released.state, plan.NEXT)
        self.assertEqual(released.owner, "")


class TestParallelWorktrees(PlanCase):
    def test_ids_do_not_collide_across_worktrees(self):
        """The allocator must see sibling worktrees before their files are committed."""
        from claude_bestpractice.gitctx import resolve

        main_ctx = self.ctx()
        plan.add(main_ctx, "on main")

        wt_ctx = resolve(self.add_worktree("feature"))
        task = plan.add(wt_ctx, "on feature")
        self.assertEqual(task.id, "0002")

    def test_tasks_are_separate_files(self):
        ctx = self.ctx()
        for i in range(5):
            plan.add(ctx, f"task {i}")
        files = list(plan.plan_dir(ctx, plan.NEXT).glob("*.md"))
        self.assertEqual(len(files), 5)

    def test_two_branches_adding_tasks_merge_without_conflict(self):
        """The property the whole substrate decision rests on."""
        ctx = self.ctx()
        plan.add(ctx, "base task")
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "base"], self.repo)

        git(["checkout", "-qb", "feature-a"], self.repo)
        plan.add(ctx, "from a")
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "a"], self.repo)

        git(["checkout", "-q", "main"], self.repo)
        git(["checkout", "-qb", "feature-b"], self.repo)
        plan.add(ctx, "from b")
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "b"], self.repo)

        git(["checkout", "-q", "main"], self.repo)
        git(["merge", "-q", "--no-edit", "feature-a"], self.repo)
        git(["merge", "-q", "--no-edit", "feature-b"], self.repo)

        titles = {t.title for t in plan.load_all(ctx)}
        self.assertEqual(titles, {"base task", "from a", "from b"})


class TestBoardRendering(PlanCase):
    def test_in_flight_comes_before_next(self):
        ctx = self.ctx()
        self.session("alpha")
        upcoming = plan.add(ctx, "later thing")
        active = plan.add(ctx, "current thing")
        plan.claim(ctx, active.id, "alpha", "main")

        rendered = plan.render_for_board(ctx)
        self.assertLess(rendered.index("IN FLIGHT"), rendered.index("NEXT"))
        self.assertIn("current thing", rendered)
        self.assertIn("later thing", rendered)

    def test_empty_plan_renders_nothing(self):
        self.assertEqual(plan.render_for_board(self.ctx()), "")

    def test_done_count_is_reported(self):
        ctx = self.ctx()
        task = plan.add(ctx, "shipped")
        plan.complete(ctx, task.id)
        plan.add(ctx, "pending")
        self.assertIn("(1 done)", plan.render_for_board(ctx))

    def test_the_board_shows_the_plan(self):
        ctx = self.ctx()
        plan.add(ctx, "visible on the board")
        proc = self.run_hook(
            "session-start", {"session_id": "s1", "hook_event_name": "SessionStart"}
        )
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("visible on the board", body)


class TestCli(PlanCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), *args],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )

    def test_add_then_list(self):
        self.assertEqual(self.run_cli("add", "Write the exporter").returncode, 0)
        out = self.run_cli("list").stdout
        self.assertIn("Write the exporter", out)
        self.assertIn("NEXT", out)

    def test_claim_and_done(self):
        self.run_cli("add", "Do the thing")
        self.assertEqual(self.run_cli("claim", "0001", "--session", "s1").returncode, 0)
        self.assertIn("DOING", self.run_cli("list").stdout)
        self.assertEqual(self.run_cli("done", "0001").returncode, 0)
        self.assertIn("DONE", self.run_cli("list").stdout)

    def test_dead_owner_is_flagged_in_the_listing(self):
        ctx = self.ctx()
        self.session("ghost", pid=999_999_999)
        task = plan.add(ctx, "orphaned")
        plan.claim(ctx, task.id, "ghost", "main")
        self.assertIn("DEAD, reclaimable", self.run_cli("list").stdout)

    def test_empty_plan_says_so(self):
        self.assertIn("plan is empty", self.run_cli("list").stdout)


if __name__ == "__main__":
    unittest.main()


class TestTheLedgerIsVisibleToGit(PlanCase):
    """A parked task that git cannot see is not parked, whatever `park` printed.

    Issue #66: an ignore rule covering Tier A hid thirty migrated tasks. Every command
    reported success, because every command asks the filesystem and the filesystem was
    fine — only git disagreed, and nothing was looking.
    """

    def hide_tier_a(self, rule: str = f"{store.TIER_A_DIRNAME}/") -> None:
        exclude = self.ctx().common_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{rule}\n")

    def test_a_healthy_repository_says_nothing(self):
        self.assertEqual(store.hidden_from_git(self.ctx()), "")

    def test_an_ignore_rule_over_tier_a_is_found_and_located(self):
        self.hide_tier_a()
        where = store.hidden_from_git(self.ctx())
        self.assertIn("info/exclude", where)
        self.assertIn(store.TIER_A_DIRNAME, where)

    def test_a_committed_file_inside_does_not_buy_an_all_clear(self):
        """The probe must be a path git can never have in its index.

        Probing the directory, or a real task file, answers "visible" as soon as one file
        inside has been committed — because a tracked path is not subject to exclude rules.
        That is a false all-clear in the case that matters most: a repository that was
        healthy once and has been hidden since. Checked against git, not reasoned about.
        """
        ctx = self.ctx()
        task = plan.add(ctx, "already committed")
        git(["add", "-f", str(task.path.relative_to(ctx.worktree_root))], ctx.worktree_root)
        git(["commit", "-qm", "commit one task before the rule appears"], ctx.worktree_root)

        self.hide_tier_a()
        self.assertNotEqual(
            store.hidden_from_git(ctx), "", "a tracked sibling masked the rule"
        )

    def park(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), "park", "Finish the importer",
             "--paths", "a.py",
             "--note", "The CSV reader lands rows but the date column stays a string; "
                       "tried strptime in the reader and it belongs in the mapper instead."],
            cwd=str(self.repo), capture_output=True, text=True, timeout=60,
        )

    def test_park_refuses_to_promise_the_task_will_be_picked_up(self):
        """`pick it up in another session` is the sentence that was false."""
        self.hide_tier_a()
        proc = self.park()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("parked", proc.stdout)
        self.assertNotIn("pick it up in another session", proc.stdout)
        self.assertIn("git cannot see", proc.stderr.lower())

    def test_park_keeps_its_promise_when_git_can_see_the_ledger(self):
        proc = self.park()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pick it up in another session", proc.stdout)
        self.assertEqual(proc.stderr, "")
