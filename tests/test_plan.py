"""The work ledger: what is done, in flight, and next — across parallel sessions."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase, git, sid

from claude_bestpractice import plan, sessions, store


class PlanCase(RepoCase):
    def aged(self, task, hours: float, state: str = plan.DOING):
        """Move this task's clock back, which is what the sweep actually reads."""
        import re

        path = task.path.parent.parent / state / task.path.name
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))
        path.write_text(re.sub(r"updated_at: .*", f"updated_at: {stamp}", path.read_text()))

    def session(self, session_id: str, pid: int | None = None) -> sessions.SessionRecord:
        rec = self.session_record(session_id, pid)
        sessions.register(self.ctx(), rec)
        return rec


class TestLifecycle(PlanCase):
    def test_add_lands_in_next(self):
        task = plan.add(self.ctx(), "Export invoices as CSV", done_when="stated", paths=["src/app.py"])
        self.assertEqual(task.state, plan.NEXT)
        self.assertEqual(task.id, "0001")
        self.assertIn("export-invoices-as-csv", task.path.name)

    def test_state_is_the_directory(self):
        """A transition is a rename, which is why parallel branches merge cleanly."""
        ctx = self.ctx()
        task = plan.add(ctx, "Ship it", done_when="stated", paths=["src/app.py"])
        self.assertTrue(task.path.parent.name == plan.NEXT)

        claimed, _ = plan.claim(ctx, task.id, "s1", "main")
        self.assertEqual(claimed.path.parent.name, plan.DOING)
        self.assertFalse(task.path.exists())

        done, _ = plan.complete(ctx, task.id)
        self.assertEqual(done.path.parent.name, plan.DONE)

    def test_ids_increment(self):
        ctx = self.ctx()
        self.assertEqual(plan.add(ctx, "first", done_when="stated", paths=["src/app.py"]).id, "0001")
        self.assertEqual(plan.add(ctx, "second", done_when="stated", paths=["src/app.py"]).id, "0002")

    def test_summary_counts_each_state(self):
        ctx = self.ctx()
        a = plan.add(ctx, "one", done_when="stated", paths=["src/app.py"])
        plan.add(ctx, "two", done_when="stated", paths=["src/app.py"])
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
        task = plan.add(ctx, "shared work", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, task.id, "alpha", "main")

        _, error = plan.claim(ctx, task.id, "beta", "feature")
        self.assertIn("held by live session", error)

    def test_a_dead_session_claim_is_taken_over(self):
        """Otherwise a crashed session leaves work marked in-flight forever."""
        ctx = self.ctx()
        self.session("ghost", pid=999_999_999)
        task = plan.add(ctx, "orphaned work", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, task.id, "ghost", "main")

        claimed, error = plan.claim(ctx, task.id, "beta", "feature")
        self.assertEqual(error, "")
        self.assertEqual(claimed.owner, "beta")

    def test_reclaiming_your_own_task_is_allowed(self):
        ctx = self.ctx()
        self.session("alpha")
        task = plan.add(ctx, "mine", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, task.id, "alpha", "main")
        _, error = plan.claim(ctx, task.id, "alpha", "main")
        self.assertEqual(error, "")

    def test_a_done_task_cannot_be_claimed(self):
        ctx = self.ctx()
        task = plan.add(ctx, "finished", done_when="stated", paths=["src/app.py"])
        plan.complete(ctx, task.id)
        _, error = plan.claim(ctx, task.id, "s1", "main")
        self.assertIn("already done", error)

    def test_reaping_releases_claims(self):
        ctx = self.ctx()
        self.session("ghost", pid=999_999_999)
        task = plan.add(ctx, "orphaned", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, task.id, "ghost", "main")

        sessions.reap(ctx)
        released = plan.find(ctx, task.id)
        self.assertEqual(released.state, plan.NEXT)
        self.assertEqual(released.owner, "")

    def test_two_claims_at_once_give_the_card_to_exactly_one_session(self):
        """Read, judged and moved with nothing held across the three: two sessions claiming
        one card both read it free, both printed "claimed", and the file named whichever
        wrote last — or one of them died on a file the other had already moved."""
        ctx = self.ctx()
        contenders = [sid(self.repo, "alpha"), sid(self.repo, "beta")]
        for contender in contenders:
            self.session(contender)
        for attempt in range(6):
            task = plan.add(ctx, f"contested {attempt}", done_when="stated", paths=["src/app.py"])
            racing = [subprocess.Popen(
                [sys.executable, str(BIN / "claude-bp-plan"), "claim", task.id, "--session", who],
                cwd=str(self.repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ) for who in contenders]
            said = [(proc.communicate(timeout=120), proc.returncode) for proc in racing]
            self.assertEqual([0, 1], sorted(code for _, code in said), said)
            self.assertFalse(any("Traceback" in err for (_, err), _ in said), said)
            loser = next(err for (_, err), code in said if code == 1)
            self.assertIn("held by live session", loser)
            winner = contenders[[code for _, code in said].index(0)]
            self.assertEqual(winner, plan.find(ctx, task.id).owner)

    def test_a_card_moved_between_the_read_and_the_rename_is_read_again(self):
        """`resume`, the sweeps and the reaper move cards without the lock. A claim that read
        the card before one of them moved it died on the file that was no longer there."""
        from unittest import mock

        ctx = self.ctx()
        task = plan.add(ctx, "on the move", done_when="stated", paths=["src/app.py"])
        stale = plan.find(ctx, task.id)
        plan.pause(ctx, task.id, "waiting on the schema decision")
        reads = []
        current = plan.find

        def find(where, task_id):
            reads.append(task_id)
            return stale if len(reads) == 1 else current(where, task_id)

        with mock.patch.object(plan, "find", find):
            claimed, error = plan.claim(ctx, task.id, "s1", "main")
        self.assertEqual("", error)
        self.assertEqual(plan.DOING, claimed.state)
        self.assertEqual(2, len(reads), "it did not read the card again")


class TestOnlyItsHolderHandsACardBack(PlanCase):
    """`claim` refused a card a live session held; `pause` and `done` took it, clearing the
    owner, and the holder's next write was refused for having nothing on the board."""

    def setUp(self) -> None:
        super().setUp()
        self.holder = sid(self.repo, "s2")
        self.session(self.holder)
        self.session(sid(self.repo, "s1"))
        self.task = plan.add(self.ctx(), "the holder's work", done_when="stated",
                             paths=["src/app.py"])
        plan.claim(self.ctx(), self.task.id, self.holder, "main")

    def cli(self, *args: str, session: str = "") -> subprocess.CompletedProcess:
        """As a session runs it, or — with no session — as the founder does at a terminal."""
        import os

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), *args],
                              capture_output=True, text=True, cwd=str(self.repo), env=env,
                              timeout=120)

    def state(self) -> tuple[str, str]:
        found = plan.find(self.ctx(), self.task.id)
        return found.state, found.owner

    def test_a_sibling_cannot_pause_it(self):
        proc = self.cli("pause", self.task.id, "--blocker", "waiting on the schema decision",
                        session="s1")
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertIn("held by live session", proc.stderr)
        self.assertEqual((plan.DOING, self.holder), self.state())

    def test_a_sibling_cannot_close_it(self):
        proc = self.cli("done", self.task.id, session="s1")
        self.assertEqual(1, proc.returncode, proc.stdout)
        self.assertIn("held by live session", proc.stderr)
        self.assertEqual((plan.DOING, self.holder), self.state())

    def test_its_holder_still_closes_it(self):
        self.assertEqual(0, self.cli("done", self.task.id, session="s2").returncode)
        self.assertEqual(plan.DONE, self.state()[0])

    def test_the_founder_at_a_terminal_is_never_asked(self):
        proc = self.cli("pause", self.task.id, "--blocker", "waiting on the schema decision")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(plan.PAUSED, self.state()[0])

    def test_a_dead_holders_card_is_anybodys_to_hand_back(self):
        self.session(self.holder, pid=999_999_999)
        proc = self.cli("pause", self.task.id, "--blocker", "waiting on the schema decision",
                        session="s1")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(plan.PAUSED, self.state()[0])


class TestParallelWorktrees(PlanCase):
    def test_ids_do_not_collide_across_worktrees(self):
        """The allocator must see sibling worktrees before their files are committed."""
        from claude_bestpractice.gitctx import resolve

        main_ctx = self.ctx()
        plan.add(main_ctx, "on main", done_when="stated", paths=["src/app.py"])

        wt_ctx = resolve(self.add_worktree("feature"))
        task = plan.add(wt_ctx, "on feature", done_when="stated", paths=["src/app.py"])
        self.assertEqual(task.id, "0002")

    def test_tasks_are_separate_files(self):
        ctx = self.ctx()
        for i in range(5):
            plan.add(ctx, f"task {i}", done_when="stated", paths=["src/app.py"])
        files = list(plan.plan_dir(ctx, plan.NEXT).glob("*.md"))
        self.assertEqual(len(files), 5)

    def test_two_branches_adding_tasks_merge_without_conflict(self):
        """The property the whole substrate decision rests on."""
        ctx = self.ctx()
        plan.add(ctx, "base task", done_when="stated", paths=["src/app.py"])
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "base"], self.repo)

        git(["checkout", "-qb", "feature-a"], self.repo)
        plan.add(ctx, "from a", done_when="stated", paths=["src/app.py"])
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "a"], self.repo)

        git(["checkout", "-q", "main"], self.repo)
        git(["checkout", "-qb", "feature-b"], self.repo)
        plan.add(ctx, "from b", done_when="stated", paths=["src/app.py"])
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
        upcoming = plan.add(ctx, "later thing", done_when="stated", paths=["src/app.py"])
        active = plan.add(ctx, "current thing", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, active.id, "alpha", "main")

        rendered = plan.render_for_board(ctx)
        self.assertLess(rendered.index("IN FLIGHT"), rendered.index("NEXT"))
        self.assertIn("current thing", rendered)
        self.assertIn("later thing", rendered)

    def test_empty_plan_renders_nothing(self):
        self.assertEqual(plan.render_for_board(self.ctx()), "")

    def test_done_count_is_reported(self):
        ctx = self.ctx()
        task = plan.add(ctx, "shipped", done_when="stated", paths=["src/app.py"])
        plan.complete(ctx, task.id)
        plan.add(ctx, "pending", done_when="stated", paths=["src/app.py"])
        self.assertIn("(1 done)", plan.render_for_board(ctx))

    def test_the_board_shows_the_plan(self):
        ctx = self.ctx()
        plan.add(ctx, "visible on the board", done_when="stated", paths=["src/app.py"])
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
        """Closing takes a finish condition, and the refusal hands over the way through.

        This is the one place the plugin used to accept an assertion: `done` was a rename,
        so a card with nothing stated about finishing was closed on the model's own word —
        which decision 0002 refuses everywhere else.
        """
        self.run_cli("add", "Do the thing")

        # Starting is where the plan is owed. `pre-tool` refuses a write no claimed card
        # covers, so refusing the claim is what makes "no code without a plan" binding —
        # and it costs the founder nothing, unlike the harness's own plan mode, which ends
        # in an approval dialog.
        unplanned = self.run_cli("claim", "0001", "--session", "s1")
        self.assertEqual(unplanned.returncode, 1)
        self.assertIn("--done-when", unplanned.stderr)
        self.assertIn("--paths", unplanned.stderr)
        self.assertIn("NEXT", self.run_cli("list").stdout, "the refusal left it unclaimed")

        self.run_cli("update", "0001", "--done-when", "the parser accepts a csv",
                     "--paths", "src/parser.py")
        self.assertEqual(self.run_cli("claim", "0001", "--session", "s1").returncode, 0)
        self.assertIn("DOING", self.run_cli("list").stdout)

        self.assertEqual(self.run_cli("done", "0001").returncode, 0)
        self.assertIn("DONE", self.run_cli("list").stdout)

    def test_queued_work_nobody_picks_up_is_set_aside_not_left_standing(self):
        """`sweep_idle` returns stalled work TO the queue and nothing ever took anything
        out, so `next` was a one-way sink: every card ever filed and not done still stood
        in it. Empty here and sixty deep in the repository the founder builds in."""
        ctx = self.ctx()
        stale = plan.add(ctx, "nobody wants this", done_when="stated", paths=["src/app.py"])
        fresh = plan.add(ctx, "filed just now", done_when="stated", paths=["src/app.py"])
        self.aged(stale, hours=30 * 24, state=plan.NEXT)

        moved = plan.sweep_queue(ctx, days=21)

        self.assertEqual([stale.id], [t.id for t in moved])
        self.assertEqual(plan.PAUSED, plan.find(ctx, stale.id).state)
        self.assertEqual(plan.NEXT, plan.find(ctx, fresh.id).state, "swept something fresh")

    def test_setting_aside_says_why_and_is_undone_by_one_command(self):
        ctx = self.ctx()
        stale = plan.add(ctx, "nobody wants this", done_when="stated", paths=["src/app.py"])
        self.aged(stale, hours=30 * 24, state=plan.NEXT)
        plan.sweep_queue(ctx, days=21)

        self.assertIn("nobody picked this up", plan.find(ctx, stale.id).blocker)
        plan.resume(ctx, stale.id)
        self.assertEqual(plan.NEXT, plan.find(ctx, stale.id).state)

    def test_the_sweep_is_off_at_zero(self):
        ctx = self.ctx()
        stale = plan.add(ctx, "nobody wants this", done_when="stated", paths=["src/app.py"])
        self.aged(stale, hours=99 * 24, state=plan.NEXT)
        self.assertEqual([], plan.sweep_queue(ctx, days=0))
        self.assertEqual(plan.NEXT, plan.find(ctx, stale.id).state)

    def test_a_live_holder_and_a_dead_one_do_not_look_alike(self):
        """The board printed the owner's id and nothing about it, so a task a chat is
        editing this minute and one abandoned three days ago read identically."""
        ctx = self.ctx()
        self.session("ghost", pid=999_999_999)
        dead = plan.add(ctx, "orphaned", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, dead.id, "ghost", "main")

        self.session("alive")
        held = plan.add(ctx, "in hand", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, held.id, "alive", "main")

        out = self.run_cli("list").stdout
        self.assertIn("reclaimable", out, "a dead holder was not flagged")
        self.assertIn("active in", out, "a live holder was not distinguished")

    def test_empty_plan_says_so(self):
        self.assertIn("plan is empty", self.run_cli("list").stdout)

    def test_a_title_that_is_one_very_long_word_is_filed(self):
        """A pasted hash or a URL with its punctuation stripped is one 300-character word,
        and the slug made from it was longer than a filename may be: `add` died with a
        traceback after the id was already allocated."""
        filed = self.run_cli("add", "x" * 300)

        self.assertEqual(0, filed.returncode, filed.stderr)
        self.assertNotIn("Traceback", filed.stderr)
        [card] = plan.load_all(self.ctx())
        self.assertLessEqual(len(card.path.name), len("0001-.md") + plan.MAX_SLUG_CHARS)
        self.assertEqual("x" * plan.MAX_TITLE_CHARS, card.title, "the slug's cap reached the title")


class TestClosingATaskSticksAcrossWorktrees(RepoCase):
    """Ten tasks closed in a worktree all came back as NEXT and stayed there: `load_all`
    reads every worktree of the clone and keeps the most advanced copy, and `next` was
    ranked above `done`. With ten worktrees one closure was outvoted by nine stale copies,
    and the board stopped being readable (#123)."""

    def two_trees(self):
        from claude_bestpractice import plan

        task = plan.add(self.ctx(), "починить импортер", paths=["src/app.py"], done_when="stated")
        self.commit("file the task")
        other = self.add_worktree("sibling")
        return task, other

    def test_done_in_one_tree_is_done_everywhere(self):
        from claude_bestpractice import plan
        from claude_bestpractice.gitctx import resolve

        task, other = self.two_trees()
        plan.complete(resolve(other), task.id)
        self.assertEqual(1, plan.summary(self.ctx())["done"])
        self.assertEqual(0, plan.summary(self.ctx())["next"])

    def test_a_claim_still_outranks_a_stale_queued_copy(self):
        """The reason the ranking existed: what is in flight is what a session must not
        collide with."""
        from claude_bestpractice import plan
        from claude_bestpractice.gitctx import resolve

        task, other = self.two_trees()
        plan.claim(resolve(other), task.id, "someone", "feat/x")
        self.assertEqual(1, plan.summary(self.ctx())["doing"])

    def test_what_another_tree_closed_is_not_offered_as_ready_to_start(self):
        """`startable` asked only for `next`, so the dedup never ran and it counted a task
        this clone had already finished."""
        from claude_bestpractice import plan
        from claude_bestpractice.gitctx import resolve

        task, other = self.two_trees()
        self.assertEqual(1, len(plan.startable(self.ctx())), "the fixture proves nothing")
        plan.complete(resolve(other), task.id)
        self.assertEqual([], plan.startable(self.ctx()))


if __name__ == "__main__":
    unittest.main()


class TestTheBoardOutlivesTheWorktreeThatWroteIt(RepoCase):
    """Issue #200. Reading has unioned the siblings since #123, so a task added in a
    worktree could be LISTED from anywhere — and the file existed in that worktree and
    nowhere else, so `git worktree remove` destroyed it. Measured on a fixture before the
    fix: listed from the main checkout, then gone from every tree after the removal.

    Fourteen real tasks were nearly lost that way and were kept only by `git add -f` by
    hand. Committing does not save them in the repository that reported this either: a
    global ignore rule covers `.claude/claude-bestpractice/`, which the plugin's own
    health line already reports, so git never held a copy.
    """

    def a_task_added_from_a_worktree(self):
        from claude_bestpractice import plan
        from claude_bestpractice.gitctx import resolve

        tree = self.add_worktree("science-first")
        task = plan.add(resolve(tree), "science first pass",
                        done_when="stated", paths=["src/app.py"])
        return task, tree

    def test_the_file_lands_in_the_main_checkout(self):
        from claude_bestpractice import plan, store

        task, tree = self.a_task_added_from_a_worktree()
        self.assertTrue(
            str(task.path).startswith(str(self.repo.resolve())),
            f"the task was written into the worktree, where removal erases it: {task.path}",
        )
        stray = tree / store.TIER_A_DIRNAME / plan.PLAN_DIR
        self.assertFalse(
            any(stray.rglob("*.md")) if stray.is_dir() else False,
            "a copy was left in the worktree as well",
        )

    def test_it_survives_the_worktree_being_removed(self):
        """The whole of the report: the board is one board per project, and it was one
        board per worktree."""
        from claude_bestpractice import plan
        from helpers import git

        task, tree = self.a_task_added_from_a_worktree()
        self.assertIn(task.id, [t.id for t in plan.load_all(self.ctx())],
                      "precondition: the task is on the board while the worktree exists")

        git(["worktree", "remove", "--force", str(tree)], self.repo)
        self.assertIn(
            task.id, [t.id for t in plan.load_all(self.ctx())],
            "the task died with the worktree that wrote it",
        )

    def test_the_command_that_writes_it_can_still_say_where(self):
        """Issue #202, one release later: `add` printed the id, wrote the file, and THEN
        raised `ValueError` from `relative_to` — the ledger root and the session's tree are
        two different directories now, and the CLI was still naming the file against the
        one it is standing in. The task existed and the command failed, which is the worst
        of both.

        Through the executable rather than the helper, because the traceback was in the
        executable and a unit test of `named_for` alone would have passed while `add`
        still crashed.
        """
        import subprocess
        import sys
        from pathlib import Path

        from helpers import BIN

        tree = self.add_worktree("science-first")
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), "add", "science first pass",
             "--paths", "src/app.py", "--done-when", "stated"],
            capture_output=True, text=True, cwd=str(tree), timeout=180,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

        # Named so the reader standing in the worktree can open it. A path relative to a
        # root they are not in resolves to nothing, which is how this went unnoticed until
        # `relative_to` happened to raise instead of misleading.
        named = proc.stdout.strip().splitlines()[-1].strip()
        self.assertTrue(Path(named).is_file(), f"{named!r} names no file from {tree}")

    def test_a_ledger_file_in_this_tree_is_still_named_the_short_way(self):
        """Which is every repository with no worktrees, and so nearly every line this
        prints. Absolute everywhere would be a regression in the common case."""
        from claude_bestpractice import plan

        ctx = self.ctx()
        task = plan.add(ctx, "in the main checkout", done_when="stated")
        self.assertEqual(
            task.path.relative_to(self.repo).as_posix(),
            plan.named_for(ctx, task.path),
        )

    def test_the_whole_lifecycle_still_runs_from_the_worktree(self):
        """The file is no longer where the session stands, and `_move` follows the FILE —
        so claiming and closing from a worktree has to keep working."""
        from claude_bestpractice import plan
        from claude_bestpractice.gitctx import resolve

        task, tree = self.a_task_added_from_a_worktree()
        elsewhere = resolve(tree)

        plan.claim(elsewhere, task.id, "s1", "t/science-first")
        self.assertEqual(1, plan.summary(self.ctx())["doing"])
        plan.complete(elsewhere, task.id)
        self.assertEqual(1, plan.summary(self.ctx())["done"])

    def test_an_add_is_never_refused_when_the_trees_cannot_be_listed(self):
        """`main_checkout` shells out to git with a timeout, so it can raise. A task
        written somewhere awkward is recoverable; a task refused is not."""
        from claude_bestpractice import plan, worktree

        def explode(_ctx):
            raise subprocess.TimeoutExpired("git worktree list", 30)

        original = worktree.main_checkout
        worktree.main_checkout = explode
        self.addCleanup(setattr, worktree, "main_checkout", original)

        task = plan.add(self.ctx(), "still filed", done_when="stated", paths=["src/app.py"])
        self.assertTrue(task.path.exists(), "the add was refused when git could not answer")

    def test_a_task_already_stranded_is_carried_home(self):
        """The repair. New tasks land in the main checkout; these are the ones already
        sitting in a tree that is about to be removed."""
        from claude_bestpractice import migrate, plan, store
        from claude_bestpractice.gitctx import resolve

        tree = self.add_worktree("science-first")
        stray = tree / store.TIER_A_DIRNAME / plan.PLAN_DIR / plan.NEXT
        stray.mkdir(parents=True)
        (stray / "0007-stranded.md").write_text(
            "---\nid: '0007'\ntitle: Stranded\nstate: next\nowner: ''\nbranch: ''\n"
            "paths: src/app.py\nsource: ''\ndone_when: stated\nblocker: ''\n---\n\n",
            encoding="utf-8",
        )

        changed = migrate.repair(resolve(tree))
        self.assertTrue(any("carry-worktree-tasks-home" in line for line in changed), changed)
        self.assertTrue(
            (self.repo / store.TIER_A_DIRNAME / plan.PLAN_DIR / plan.NEXT
             / "0007-stranded.md").exists(),
            "the stranded task was not carried to the main checkout",
        )
        self.assertFalse(list(stray.glob("*.md")), "a copy was left behind in the worktree")

    def test_the_repair_never_clobbers_a_more_advanced_copy(self):
        """The reader ranks copies by how far the lifecycle carried them, so overwriting a
        `done/` copy with a stale `next/` one is the reversal #123 fixed, re-entered."""
        from claude_bestpractice import migrate, plan, store
        from claude_bestpractice.gitctx import resolve

        task = plan.add(self.ctx(), "already closed", done_when="stated", paths=["src/app.py"])
        plan.claim(self.ctx(), task.id, "s1", "main")
        plan.complete(self.ctx(), task.id)

        tree = self.add_worktree("science-first")
        stray = tree / store.TIER_A_DIRNAME / plan.PLAN_DIR / plan.NEXT
        stray.mkdir(parents=True)
        (stray / task.path.name).write_text("---\nid: '0001'\nstate: next\n---\n", encoding="utf-8")

        migrate.repair(resolve(tree))
        self.assertFalse(
            (self.repo / store.TIER_A_DIRNAME / plan.PLAN_DIR / plan.NEXT / task.path.name).exists(),
            "a stale queued copy was carried over the closed one",
        )
        self.assertEqual(1, plan.summary(self.ctx())["done"])


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
        task = plan.add(ctx, "already committed", done_when="stated", paths=["src/app.py"])
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


class TestWhetherAChatIsOnIt(PlanCase):
    """Activity is DERIVED, never stored.

    A stored "in progress" flag is written by a session that then crashes, and stays true
    forever — which is exactly the case the reader needs it for. The registry already knows
    who is alive, so the ledger asks it rather than keeping a second copy that can disagree.
    """

    def test_a_live_holder_reads_as_active(self):
        ctx = self.ctx()
        self.session("alpha")
        task = plan.add(ctx, "current work", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, task.id, "alpha", "main")
        self.assertIn("active in", plan.activity(ctx, plan.find(ctx, task.id)))

    def test_a_dead_holder_reads_as_reclaimable(self):
        ctx = self.ctx()
        self.session("ghost", pid=999_999_999)
        task = plan.add(ctx, "abandoned", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, task.id, "ghost", "main")
        self.assertIn("reclaimable", plan.activity(ctx, plan.find(ctx, task.id)))

    def test_an_unclaimed_task_claims_nothing(self):
        ctx = self.ctx()
        task = plan.add(ctx, "nobody's", done_when="stated", paths=["src/app.py"])
        self.assertEqual("", plan.activity(ctx, task))

    def test_nothing_about_activity_is_written_to_the_file(self):
        """The whole point. A file that carries it can carry it wrongly."""
        ctx = self.ctx()
        self.session("alpha")
        task = plan.add(ctx, "current work", done_when="stated", paths=["src/app.py"])
        plan.claim(ctx, task.id, "alpha", "main")
        text = plan.find(ctx, task.id).path.read_text(encoding="utf-8")
        self.assertNotIn("active", text)


class TestPausingSaysWhatWouldLiftIt(PlanCase):
    def test_a_pause_without_a_blocker_is_refused(self):
        ctx = self.ctx()
        task = plan.add(ctx, "blocked work", done_when="stated", paths=["src/app.py"])
        paused, problem = plan.pause(ctx, task.id, "later")
        self.assertIsNone(paused)
        self.assertIn("what would lift it", problem)

    def test_a_paused_task_leaves_the_queue_and_says_why(self):
        """`next` means pick me up. A task waiting on somebody else's merge says the
        opposite, and conflating them sends session after session at work that cannot
        move."""
        ctx = self.ctx()
        task = plan.add(ctx, "blocked work", done_when="stated", paths=["src/app.py"])
        paused, problem = plan.pause(ctx, task.id, "waiting on the schema decision in #41")
        self.assertEqual("", problem)
        self.assertEqual(plan.PAUSED, paused.state)

        board = plan.render_for_board(ctx)
        self.assertIn("PAUSED:", board)
        self.assertIn("schema decision", board)
        self.assertNotIn("NEXT:", board, "a paused task was still offered as work to take")

    def test_resuming_clears_the_blocker(self):
        ctx = self.ctx()
        task = plan.add(ctx, "blocked work", done_when="stated", paths=["src/app.py"])
        plan.pause(ctx, task.id, "waiting on the schema decision in #41")
        resumed, problem = plan.resume(ctx, task.id)
        self.assertEqual("", problem)
        self.assertEqual(plan.NEXT, resumed.state)
        self.assertEqual("", resumed.blocker)


class TestATaskCanLearnThingsWhileItWaits(PlanCase):
    def test_updating_keeps_the_identity(self):
        ctx = self.ctx()
        task = plan.park(ctx, "the importer", body="x" * 90, paths=["a.py"])
        amended, problem = plan.amend(
            ctx, task.id, note="y" * 90, paths=["b.py"], done_when="the CSV round-trips",
        )
        self.assertEqual("", problem)
        self.assertEqual(task.id, amended.id)
        self.assertEqual(["b.py"], amended.paths)
        self.assertEqual("the CSV round-trips", amended.done_when)

    def test_the_finish_condition_survives_a_transition(self):
        """A move that forgets it hands the next session the thin task the ledger exists
        to prevent."""
        ctx = self.ctx()
        task = plan.park(ctx, "the importer", body="x" * 90, paths=["a.py"])
        plan.amend(ctx, task.id, done_when="the CSV round-trips")
        plan.claim(ctx, task.id, "s1", "main")
        self.assertEqual("the CSV round-trips", plan.find(ctx, task.id).done_when)

    def test_the_handoff_view_leads_with_the_finish_condition(self):
        ctx = self.ctx()
        task = plan.park(ctx, "the importer", body="x" * 90, paths=["a.py"])
        plan.amend(ctx, task.id, done_when="the CSV round-trips")
        shown = plan.show(plan.find(ctx, task.id))
        self.assertIn("DONE WHEN:", shown)
        self.assertLess(shown.index("DONE WHEN:"), shown.index("HANDOFF:"))


class TestTasksThatAreNotIndependent(PlanCase):
    """A research session produces work with an order in it, and the ledger was flat.

    Two changes that individually swing the result the wrong way and only mean something
    shipped together; a task that is simply wrong until an earlier one lands. None of it
    was expressible, so it went into a markdown section and was hoped to be read — which
    is the failure the ledger exists to end, one level up (#104).
    """

    def test_an_order_survives_the_transition_that_records_it(self):
        """Every field added to this model has been dropped by a move at least once."""
        ctx = self.ctx()
        first = plan.add(ctx, "fix the prohibited flag", done_when="stated", paths=["src/app.py"])
        second = plan.add(ctx, "score zero for prohibited only", after=[first.id],
                          together=["0009"], paths=["backend/app.py"], done_when="stated")

        claimed, error = plan.claim(ctx, second.id, "s1", "feat/x")
        self.assertFalse(error, error)
        self.assertEqual([first.id], claimed.after, "the order was lost on claim")
        self.assertEqual(["0009"], claimed.together)
        self.assertEqual(["backend/app.py"], claimed.paths)

    def test_reclaiming_a_dead_sessions_task_keeps_what_it_knew(self):
        """The reclaim path rewrote the document from its title alone — handing the next
        session a thin task at the moment it has least context."""
        ctx = self.ctx()
        task = plan.add(ctx, "the crashed session's work", after=["0001"],
                        paths=["backend/app.py"], done_when="the suite is green")
        plan.claim(ctx, task.id, "ghost", "feat/x")

        plan.release(ctx, "ghost")

        back = plan.find(ctx, task.id)
        self.assertEqual(plan.NEXT, back.state)
        self.assertEqual(["0001"], back.after, "the order was dropped by the reclaim")
        self.assertEqual(["backend/app.py"], back.paths)
        self.assertEqual("the suite is green", back.done_when)

    def test_an_id_that_names_nothing_blocks_rather_than_clears(self):
        """Waiting on a task that does not exist is waiting forever; reading that as
        clear would be the silent failure rather than the visible one."""
        ctx = self.ctx()
        task = plan.add(ctx, "waits on a typo", after=["9999"], done_when="stated", paths=["src/app.py"])
        self.assertEqual(["9999"], plan.blockers(ctx, task))

    def test_a_blocker_clears_when_the_earlier_task_lands(self):
        ctx = self.ctx()
        first = plan.add(ctx, "lands first", done_when="stated", paths=["src/app.py"])
        second = plan.add(ctx, "comes after", after=[first.id], done_when="stated", paths=["src/app.py"])
        self.assertEqual([first.id], plan.blockers(ctx, second))

        plan.complete(ctx, first.id)
        self.assertEqual([], plan.blockers(ctx, plan.find(ctx, second.id)))

    def test_startable_answers_what_can_i_begin_right_now(self):
        """The question an implementing session opens with, answerable without reading a
        design document — which is the acceptance criterion the issue names."""
        ctx = self.ctx()
        first = plan.add(ctx, "lands first", done_when="stated", paths=["src/app.py"])
        plan.add(ctx, "comes after", after=[first.id], done_when="stated", paths=["src/app.py"])
        plan.add(ctx, "independent", done_when="stated", paths=["src/app.py"])

        startable = {t.title for t in plan.startable(ctx)}
        self.assertEqual({"lands first", "independent"}, startable)

    def test_the_waiter_is_told_whichever_way_the_id_was_typed(self):
        """`claude-bp-plan done 7` is how the id is typed and `0007` is how `after` files it.
        The unpadded one was compared as typed, so the session waiting on the card was told
        nothing, while `done 0007` told it at once."""
        from claude_bestpractice import inbox

        ctx = self.ctx()
        first = plan.add(ctx, "lands first", done_when="stated", paths=["src/app.py"])
        second = plan.add(ctx, "comes after", after=[first.id], done_when="stated",
                          paths=["src/other.py"])
        self.session("waiter")
        plan.claim(ctx, second.id, "waiter", "main")

        done = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), "done", first.id.lstrip("0")],
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )

        self.assertEqual(0, done.returncode, done.stderr)
        told = [n["text"] for n in inbox.pending(ctx, "waiter")]
        self.assertTrue(any("no longer blocked" in text for text in told), told)


class TestAValueStaysOnItsOwnLine(PlanCase):
    """Three dashes inside a title, a finish condition or a blocker cut the card at them,
    because the front matter ended at the first `---` anywhere: the title read back short,
    the rest of the keys became the handoff note, and `claim` refused a planned card as
    unplanned. A newline inside a value ended it the same way."""

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        command = [sys.executable, str(BIN / "claude-bp-plan"), *args]
        return subprocess.run(command, capture_output=True, text=True, cwd=str(self.repo),
                              timeout=120)

    def test_a_title_with_three_dashes_reads_back_whole_and_can_be_claimed(self):
        self.cli("add", "Split parser --- phase 2", "--paths", "src/parser.py",
                 "--done-when", "both phases pass --- including the empty file")

        shown = self.cli("show", "1").stdout
        self.assertIn("Split parser --- phase 2", shown)
        self.assertIn("both phases pass --- including the empty file", shown)
        self.assertNotIn("state:", shown, "the front matter leaked into the handoff")
        claimed = self.cli("claim", "1", "--session", "s1")
        self.assertEqual(0, claimed.returncode, claimed.stderr)

    def test_a_blocker_with_three_dashes_is_not_the_handoff(self):
        self.cli("add", "Wire the vendor feed", "--paths", "src/feed.py", "--done-when", "ok")
        self.cli("pause", "1", "--blocker", "waiting on --- the vendor's API key")

        card = plan.find(self.ctx(), "1")
        self.assertEqual("waiting on --- the vendor's API key", card.blocker)
        self.assertEqual(plan.NO_DETAIL, card.body)
        self.assertEqual(["src/feed.py"], card.paths)

    def test_a_newline_in_a_value_cannot_become_a_key(self):
        task = plan.add(self.ctx(), "Third\nstate: done\nowner: somebody", paths=["src/a.py"],
                        done_when="stated")

        card = plan.find(self.ctx(), task.id)
        self.assertEqual(plan.NEXT, card.state)
        self.assertEqual("", card.owner, "a title wrote the owner")
        self.assertEqual("Third state: done owner: somebody", card.title)

    def test_the_founders_card_keeps_where_it_came_from(self):
        """Their sentence carried the dashes, the card lost `source`, and the next message
        filed a second card beside the orphan."""
        plan.open_for(self.ctx(), "split the parser --- phase 2 first", "s1", "h1")
        plan.open_for(self.ctx(), "and then the exporter", "s1", "h1")

        cards = plan.load_all(self.ctx())
        self.assertEqual(1, len(cards), [c.title for c in cards])
        self.assertEqual(plan.FROM_THE_FOUNDER, cards[0].source)


class TestTheOrderIsVisibleWithoutOpeningTheTask(PlanCase):
    def plan_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
        )

    def test_list_marks_what_is_waiting_and_counts_what_is_ready(self):
        ctx = self.ctx()
        first = plan.add(ctx, "lands first", done_when="stated", paths=["src/app.py"])
        plan.add(ctx, "comes after", after=[first.id], done_when="stated", paths=["src/app.py"])

        out = self.plan_cli("list").stdout
        self.assertIn(f"[after {first.id}]", out)
        self.assertIn("1 ready to start", out)

    def test_claim_says_the_earlier_task_has_not_landed(self):
        ctx = self.ctx()
        first = plan.add(ctx, "lands first", done_when="stated", paths=["src/app.py"])
        second = plan.add(ctx, "comes after", after=[first.id], done_when="stated", paths=["src/app.py"])

        proc = self.plan_cli("claim", second.id)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn(first.id, proc.stderr)
        self.assertIn("has not landed", proc.stderr)

    def test_add_carries_the_same_fields_park_does(self):
        """Nothing marked `add` as the impoverished one, so thirteen tasks were filed
        with it and their files backfilled by hand afterwards."""
        proc = self.plan_cli("add", "with everything", "--paths", "backend/app.py",
                             "--done-when", "the suite is green", "--after", "0001")
        self.assertEqual(0, proc.returncode, proc.stderr)
        task = plan.load_all(self.ctx())[-1]
        self.assertEqual(["backend/app.py"], task.paths)
        self.assertEqual("the suite is green", task.done_when)
        self.assertEqual(["0001"], task.after)

    def test_a_bare_add_points_at_the_command_that_carries_context(self):
        self.assertIn("park", self.plan_cli("add", "a bare title").stdout)


class TestWorkThatStoppedMoving(PlanCase):
    """`reap` covers the session that DIED. Nothing covered the commoner case: a live
    chat that claimed 0007, moved on to something else, and left it reading `doing` on
    every board for the rest of the week. The board's whole claim is that it says what is
    in flight, and a row nobody is working on is that claim being false.
    """

    def claimed_by(self, session_id: str, touching: list[str], paths: list[str]):
        rec = self.session_record(session_id)
        rec.last_touched = touching
        sessions.register(self.ctx(), rec)
        task = plan.add(self.ctx(), "the task", paths=paths, done_when="stated")
        plan.claim(self.ctx(), task.id, session_id, "feat/x")
        return task

    def test_a_task_untouched_past_the_threshold_returns_to_the_queue(self):
        task = self.claimed_by("wandered", touching=["other.py"], paths=["app.py"])
        self.aged(task, 30)

        moved = plan.sweep_idle(self.ctx(), 24.0)

        self.assertEqual([task.id], [t.id for t in moved])
        back = plan.find(self.ctx(), task.id)
        self.assertEqual(plan.NEXT, back.state)
        self.assertIn("returned to the queue", back.body,
                      "the next session must not have to rediscover why it moved")

    def test_a_session_still_working_on_it_keeps_it(self):
        """Reclaiming work mid-change is worse than the stale row it was meant to fix."""
        task = self.claimed_by("busy", touching=["app.py"], paths=["app.py"])
        self.aged(task, 30)

        self.assertEqual([], plan.sweep_idle(self.ctx(), 24.0))
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_a_task_that_moved_recently_is_left_alone(self):
        task = self.claimed_by("recent", touching=["other.py"], paths=["app.py"])

        self.assertEqual([], plan.sweep_idle(self.ctx(), 24.0))
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_the_threshold_is_the_founders_to_move(self):
        task = self.claimed_by("wandered", touching=["other.py"], paths=["app.py"])
        self.aged(task, 3)

        self.assertEqual([], plan.sweep_idle(self.ctx(), 24.0))
        self.assertEqual([task.id], [t.id for t in plan.sweep_idle(self.ctx(), 2.0)])

    def test_a_session_working_on_it_from_its_own_tree_keeps_it(self):
        """Claimed in the main checkout, worked on from the tree it was sent to: every
        touch lands on the tree's id, and the id the card names never moves again."""
        from claude_bestpractice.gitctx import resolve

        from helpers import session_record_for

        task = self.claimed_by(sid(self.repo, "s1"), touching=[], paths=["app.py"])
        tree = self.add_worktree("feat-x")
        there = session_record_for(resolve(tree), sid(tree, "s1"))
        there.last_touched = ["app.py"]
        sessions.register(resolve(tree), there)
        self.aged(task, 30)

        self.assertEqual([], plan.sweep_idle(self.ctx(), 24.0))
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)


class TestTheBoardLearnsTheTaskWhenItArrives(RepoCase):
    """The demand fired at the first WRITE, so between "the founder gave a task" and "the
    session touched a file" the board said nothing — and every sibling deciding what was
    safe to touch read an empty board while somebody was already working.

    In practice the card got filed because a gate refused, which makes it a description of
    work already done rather than a claim on work about to happen (#165).
    """

    def say(self, prompt: str, session: str = "s1"):
        return self.run_hook("prompt-capture", {
            "session_id": session, "hook_event_name": "UserPromptSubmit", "prompt": prompt,
        })

    def board(self, state):
        from claude_bestpractice import plan

        return plan.load_all(self.ctx(), state)

    def test_a_task_reaches_the_board_before_any_file_is_touched(self):
        self.say("перепиши импортер так, чтобы он не падал на пустом CSV")
        titles = [t.title for t in self.board("next")]
        self.assertEqual(1, len(titles), titles)
        self.assertIn("импортер", titles[0])

    def test_it_is_not_claimed_because_the_plan_is_not_known_yet(self):
        """Claiming needs `done_when` and the paths, and neither is knowable before the
        session has looked at anything. A card guessed then is worse than a late one."""
        self.say("перепиши импортер")
        self.assertEqual([], self.board("doing"))

    def test_three_messages_about_one_task_leave_one_card(self):
        """The ledger is only worth reading while it does not drift."""
        for said in ("почини импортер", "и заодно посмотри логи", "начни с тестов"):
            self.say(said)
        self.assertEqual(1, len(self.board("next")))

    def test_a_later_instruction_retitles_the_card(self):
        """The first message is often a remark rather than the work. «потом как все задачи
        на доске доделаю» cleared the bar and sat on the board as a task — the drift the
        ledger exists not to have. The statement already follows the founder; the card had
        no reason not to."""
        self.say("потом как все задачи на доске доделаю")
        self.say("чини 0126 — make migrate в worktree зовёт CLI из главного checkout")
        titles = [t.title for t in self.board("next")]
        self.assertEqual(1, len(titles), titles)
        self.assertIn("0126", titles[0])

    def test_a_claimed_card_is_never_retitled(self):
        """Claiming means a session wrote a plan. Overwriting its title with whatever was
        said next is clobbering work with conversation."""
        from claude_bestpractice import plan

        self.say("почини импортер")
        card = self.board("next")[0]
        plan.amend(self.ctx(), card.id, paths=["importer.py"], done_when="stated")
        plan.claim(self.ctx(), card.id, sid(self.repo, "s1"), self.ctx().branch)

        self.say("а теперь совсем другое: посмотри логи деплоя")
        doing = self.board("doing")
        self.assertEqual(1, len(doing))
        self.assertIn("импортер", doing[0].title)

    def test_a_session_already_working_gets_no_second_card(self):
        from claude_bestpractice import plan

        task = plan.add(self.ctx(), "what this session is already doing",
                        paths=["a.py"], done_when="stated")
        plan.claim(self.ctx(), task.id, sid(self.repo, "s1"), self.ctx().branch)
        self.say("а теперь ещё вот это")
        self.assertEqual([], self.board("next"))

    def test_a_paste_does_not_become_a_task(self):
        """A card is read by other sessions as a claim, so the bar is higher than the task
        statement's: a pasted deploy tail on the board is exactly the drift the ledger
        exists not to have.

        Two prompt lines, because one traceback line is deliberately NOT a paste here —
        "this failed, look: <traceback>" is an instruction with evidence attached, and
        `is_pasted_output` says so.
        """
        self.say("hedge@AVANTURER-PC:~/dev$ make deploy\n  building…\n"
                 "hedge@AVANTURER-PC:~/dev$ echo done")
        self.assertEqual([], self.board("next"))

    def test_the_card_says_where_it_came_from(self):
        """So `list` does not present the founder's own words as a plan somebody wrote."""
        from claude_bestpractice import plan

        self.say("почини импортер")
        self.assertEqual(plan.FROM_THE_FOUNDER, self.board("next")[0].source)

    def test_a_second_session_does_not_retitle_the_first_ones_card(self):
        """Every session starts in the main checkout on the trunk, and the BRANCH used to
        decide whose card this was: the second session's first message retitled the first
        session's card, and the first session's work left the board."""
        self.say("add a CSV export to the billing report", session="s1")
        self.say("rewrite the login form validation", session="s2")
        titles = sorted(t.title for t in self.board("next"))
        self.assertEqual(["add a CSV export to the billing report",
                          "rewrite the login form validation"], titles)

    def test_the_card_follows_its_own_session_into_a_worktree(self):
        """The harness id survives the move; the composed session id does not."""
        from claude_bestpractice import plan

        self.say("почини импортер", session="s1")
        tree = self.add_worktree("feat-importer")
        self.run_hook("prompt-capture", {
            "session_id": "s1", "hook_event_name": "UserPromptSubmit",
            "prompt": "а теперь почини импортер для пустого CSV",
        }, cwd=tree)
        cards = self.board("next")
        self.assertEqual(1, len(cards), [c.title for c in cards])
        self.assertIn("пустого CSV", cards[0].title)
        self.assertEqual(cards[0].id, plan.opened_for(self.ctx(), "s1").id)

    def test_a_transition_keeps_who_opened_the_card(self):
        from claude_bestpractice import plan

        self.say("почини импортер", session="s1")
        card = self.board("next")[0]
        moved, _ = plan.amend(self.ctx(), card.id, paths=["importer.py"], done_when="stated")
        self.assertEqual("s1", moved.opened_by)
        claimed, _ = plan.claim(self.ctx(), card.id, sid(self.repo, "s1"), self.ctx().branch)
        self.assertEqual("s1", claimed.opened_by)
        self.assertIsNone(plan.opened_for(self.ctx(), "s1"), "a claimed card is not unclaimed")

    def test_the_finish_names_the_card_instead_of_asking_for_another(self):
        """Told to `add`, a real session filed a duplicate and left its own card in NEXT
        over finished work — an invitation to the next session to do the job again."""
        self.say("почини импортер", session="s1")
        card = self.board("next")[0]
        self.write("importer.py", "x = 1\n")
        proc = self.run_hook("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop"})
        self.assertIn(f"claude-bp-plan claim {card.id}", proc.stderr)
        self.assertIn(f"claude-bp-plan update {card.id} --paths importer.py", proc.stderr)
        self.assertNotIn("claude-bp-plan add", proc.stderr)


class TestDeliveryClosesTheCard(PlanCase):
    """The ledger had no closing half: `complete` had one caller, the CLI, so a card left
    `doing` only if somebody remembered the command. Nothing ever did, and a row saying
    work is in flight over work that shipped is the board asserting a collision that
    cannot happen — the same lie the reaper exists to stop telling from the other end.
    """

    def in_flight(self, session_id: str = "s1", *paths: str, title: str = "the work"):
        ctx = self.ctx()
        task = plan.add(ctx, title, paths=list(paths) or ["src/app.py"], done_when="stated")
        claimed, error = plan.claim(ctx, task.id, session_id, ctx.branch)
        self.assertEqual("", error)
        return claimed

    def test_a_delivery_closes_the_card_it_carried(self):
        task = self.in_flight("s1", "src/app.py")
        closed = plan.settle_delivered(self.ctx(), "s1", ["src/app.py", "README.md"], "the merge")
        self.assertEqual([task.id], [t.id for t in closed])
        self.assertEqual(plan.DONE, plan.find(self.ctx(), task.id).state)

    def test_a_card_the_delivery_did_not_carry_stays_in_flight(self):
        task = self.in_flight("s1", "src/billing.py")
        self.assertEqual([], plan.settle_delivered(self.ctx(), "s1", ["src/app.py"], "the merge"))
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_a_siblings_card_is_never_closed(self):
        """It may be mid-change on top of what just landed, and taking its row off the
        board is the same lie in the other direction."""
        task = self.in_flight("s2", "src/app.py")
        self.assertEqual([], plan.settle_delivered(self.ctx(), "s1", ["src/app.py"], "the merge"))
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_a_directory_a_card_named_matches_what_shipped_under_it(self):
        """What gets written when the work is a subsystem rather than a file."""
        task = self.in_flight("s1", "plugin/lib")
        closed = plan.settle_delivered(
            self.ctx(), "s1", ["plugin/lib/claude_bestpractice/plan.py"], "the merge")
        self.assertEqual([task.id], [t.id for t in closed])

    def test_a_card_is_closed_on_any_of_its_files_not_all_of_them(self):
        """A card names the code and the test that proves it; a delivery that carried the
        change and not the fixture is still the delivery of that work. Demanding the whole
        list closes almost nothing, which is the failure this exists to end."""
        task = self.in_flight("s1", "src/app.py", "tests/test_app.py", "docs/app.md")
        closed = plan.settle_delivered(self.ctx(), "s1", ["src/app.py"], "the merge")
        self.assertEqual([task.id], [t.id for t in closed])

    def test_what_closed_it_is_written_into_the_card(self):
        """The founder reads outcomes, so "who decided this was finished" has to be
        answerable from the file itself."""
        task = self.in_flight("s1", "src/app.py")
        plan.settle_delivered(self.ctx(), "s1", ["src/app.py"], "the merge of feat/x")
        body = plan.find(self.ctx(), task.id).path.read_text(encoding="utf-8")
        self.assertIn("closed on delivery", body)
        self.assertIn("the merge of feat/x", body)
        self.assertIn("src/app.py", body)

    def test_a_delivery_that_carried_nothing_closes_nothing(self):
        task = self.in_flight("s1", "src/app.py")
        self.assertEqual([], plan.settle_delivered(self.ctx(), "s1", [], "the merge"))
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_a_queued_card_is_not_closed_by_somebody_elses_delivery(self):
        """Only what is in flight. A card nobody has started is not work that shipped."""
        ctx = self.ctx()
        task = plan.add(ctx, "not started", paths=["src/app.py"], done_when="stated")
        self.assertEqual([], plan.settle_delivered(ctx, "s1", ["src/app.py"], "the merge"))
        self.assertEqual(plan.NEXT, plan.find(ctx, task.id).state)

    def test_the_demand_names_the_card_and_both_ways_out(self):
        """`pause` beside `done` is not a formality: work can reach the base branch and
        still not be finished, and a gate whose only exit is "declare it done" buys a
        clean board by making the ledger lie."""
        task = self.in_flight("s1", "src/app.py", title="the thing that shipped")
        demand = plan.closure_demand(plan.held_by(self.ctx(), "s1"))
        self.assertIn(task.id, demand)
        self.assertIn("the thing that shipped", demand)
        self.assertIn("claude-bp-plan done", demand)
        self.assertIn("claude-bp-plan pause", demand)
        self.assertIn("Do not ask the founder", demand)

    def test_held_by_answers_for_one_session_only(self):
        mine = self.in_flight("s1", "src/app.py")
        self.in_flight("s2", "src/other.py")
        self.assertEqual([mine.id], [t.id for t in plan.held_by(self.ctx(), "s1")])


class TestATransitionIsARenameInGitToo(PlanCase):
    """A tracked task file that moves must not leave git guessing (#208), and must not be
    carried back into it (decision 0018).

    The founder's global ignore covers `.claude/claude-bestpractice/`, so a state the
    repository never committed — `paused` — is invisible to git. Moving a committed task
    into it deleted a tracked file and created a hidden one: fifty unstaged `D` rows in a
    working checkout, each one indistinguishable from lost work. The answer was to stage a
    rename with `add -f`, and that is how the ledger came back into git after it was taken
    out: the path the card left is taken out of the index now, and nothing is added.
    """

    def ignore_the_ledger(self) -> None:
        """The founder's rule, in the one place a fixture may write it."""
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(".claude/claude-bestpractice/\n", encoding="utf-8")

    def committed_task(self):
        task = plan.add(self.ctx(), "Do a thing", done_when="stated", paths=["src/app.py"])
        self.commit("ledger")
        self.ignore_the_ledger()
        return task

    def test_pausing_a_committed_task_leaves_no_phantom_deletion(self):
        task = self.committed_task()
        paused, _ = plan.pause(self.ctx(), task.id, "waiting on the API key")

        unstaged = [line for line in git(["status", "--porcelain"], self.repo).splitlines()
                    if line[1:2] == "D"]
        self.assertEqual([], unstaged, "a bare deletion git was never told about")
        self.assertTrue(paused.path.is_file(), "the paused card left the disk")

    def test_the_card_leaves_the_index_rather_than_moving_in_it(self):
        """A staged rename is the ledger being put back into git by the next commit."""
        task = self.committed_task()
        plan.claim(self.ctx(), task.id, sid(self.repo, "s1"), "main")

        staged = git(["diff", "--cached", "--name-status"], self.repo).splitlines()
        self.assertEqual([f"D\t{task.path.relative_to(self.repo).as_posix()}"], staged)
        self.assertEqual("", git(["ls-files", ".claude/claude-bestpractice/plan"], self.repo))

    def test_a_tree_whose_trunk_still_tracks_the_cards_commits_no_card(self):
        """The shape reported: a tree cut after the upgrade from a trunk that still tracked
        its cards, one `done` there, and the tree's next commit carrying the card back."""
        task = self.committed_task()
        tree = self.add_worktree("feat-x")
        done = subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), "done", task.id],
                              capture_output=True, text=True, cwd=str(tree), timeout=120)
        self.assertEqual(0, done.returncode, done.stderr)

        git(["commit", "-qm", "the work in that tree"], tree)
        self.assertEqual("", git(["ls-files", ".claude/claude-bestpractice/plan"], tree),
                         "the card went back into git with the tree's commit")

    def test_an_untracked_ledger_is_not_quietly_added_to_git(self):
        """Preserving what the founder tracks is not the same as granting a place in it."""
        self.ignore_the_ledger()
        task = plan.add(self.ctx(), "Do a thing", done_when="stated", paths=["src/app.py"])
        plan.pause(self.ctx(), task.id, "waiting on the API key")

        self.assertEqual("", git(["diff", "--cached", "--name-only"], self.repo))


class TestATransitionReachesEveryCopy(RepoCase):
    """Issues #210 and #212. A transition moved the copy the command could see and no
    other, so a card closed from one tree stayed `next` or `paused` in every sibling: the
    board read the advanced copy and looked right, `done` printed success, and the moment
    the tree holding that copy was removed the task came back from the dead.

    #212 is the same defect with a different slug on the stale copy — dedup is by filename,
    so a copy amended in another tree is not deduplicated away and `list` went on showing
    the task as waiting after `done` reported it closed.
    """

    def a_task_copied_into_a_sibling(self, state: str, title: str = ""):
        from claude_bestpractice import plan, store

        task = plan.add(self.ctx(), "починить импортер", paths=["src/app.py"], done_when="stated")
        tree = self.add_worktree("sibling")
        stale = tree / store.TIER_A_DIRNAME / plan.PLAN_DIR / state
        stale.mkdir(parents=True)
        text = task.path.read_text(encoding="utf-8")
        name = task.path.name
        if title:
            text = text.replace("title: починить импортер", f"title: {title}")
            name = f"{task.id}-{plan.slug(title)}.md"
        (stale / name).write_text(text, encoding="utf-8")
        return task, tree

    def test_done_closes_the_copy_in_every_tree(self):
        from claude_bestpractice import plan

        task, tree = self.a_task_copied_into_a_sibling(plan.PAUSED)
        plan.complete(self.ctx(), task.id)
        self.assertEqual(
            [plan.DONE], sorted({c.state for c in plan.copies(self.ctx(), task.id)}),
            "a copy was left behind in a sibling worktree",
        )

    def test_a_closed_task_stops_being_listed_as_waiting(self):
        """The report verbatim: `done` printed success and `list` showed the task still
        paused, because the stale copy carried a different slug and survived the dedup."""
        from claude_bestpractice import plan

        task, tree = self.a_task_copied_into_a_sibling(plan.PAUSED, title="importer, renamed")
        plan.complete(self.ctx(), task.id)
        self.assertEqual([], plan.load_all(self.ctx(), plan.PAUSED))
        self.assertEqual(0, plan.summary(self.ctx())[plan.PAUSED])

    def test_the_closure_outlives_the_tree_it_was_made_from(self):
        from claude_bestpractice import plan
        from claude_bestpractice.gitctx import resolve
        from helpers import git

        task, tree = self.a_task_copied_into_a_sibling(plan.NEXT)
        plan.complete(resolve(tree), task.id)
        git(["worktree", "remove", "--force", str(tree)], self.repo)
        self.assertEqual(
            [plan.DONE], [t.state for t in plan.load_all(self.ctx())],
            "the closure died with the worktree that made it",
        )

    def test_a_note_reaches_every_copy(self):
        from claude_bestpractice import plan

        task, tree = self.a_task_copied_into_a_sibling(plan.NEXT)
        plan.amend(self.ctx(), task.id, note="the importer needs the new schema first")
        for copy in plan.copies(self.ctx(), task.id):
            self.assertIn("new schema", copy.body, f"{copy.path} never heard the note")

    def test_a_note_keeps_the_order_the_task_was_written_in(self):
        from claude_bestpractice import plan

        first = plan.add(self.ctx(), "schema", paths=["src/app.py"], done_when="stated")
        second = plan.add(self.ctx(), "importer", paths=["src/app.py"], done_when="stated",
                          after=[first.id])
        amended, _ = plan.amend(self.ctx(), second.id, note="needs the new schema first")
        self.assertEqual([first.id], amended.after, "the amendment cut the task loose")

    def test_reconcile_brings_an_older_ledger_up_to_the_board(self):
        """The repair for ledgers the old transitions scattered."""
        from claude_bestpractice import plan

        task, tree = self.a_task_copied_into_a_sibling(plan.NEXT)
        plan.reconcile_copies(self.ctx())
        self.assertEqual([plan.NEXT], sorted({c.state for c in plan.copies(self.ctx(), task.id)}))

        plan.claim(self.ctx(), task.id, "s1", "main")
        self.assertEqual(1, plan.reconcile_copies(self.ctx()))
        self.assertEqual([plan.DOING], sorted({c.state for c in plan.copies(self.ctx(), task.id)}))


class TestOneCardInAnotherEncodingStopsNothing(PlanCase):
    """A card saved in cp1251 raised UnicodeDecodeError out of every reader of the ledger:
    `claude-bp-plan list` died on a traceback, and every Write in every session was refused
    with "gate failed (UnicodeDecodeError…)", naming neither the file nor a way out."""

    def setUp(self) -> None:
        super().setUp()
        ours = plan.add(self.ctx(), "Fix the parser", paths=["a.py"], done_when="stated")
        (ours.path.parent / "0002-importer.md").write_bytes(
            "---\ntitle: Починить импорт\npaths: b.py\ndone_when: цены\n---\n\nПочинить импорт\n"
            .encode("cp1251"))

    def test_the_ledger_still_reads_and_says_which_card(self):
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), "list"],
                              capture_output=True, text=True, cwd=str(self.repo), timeout=120)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("0002", proc.stdout)
        self.assertEqual(["b.py"], plan.find(self.ctx(), "0002").paths)

    def test_a_write_is_judged_rather_than_refused_on_a_codec(self):
        proc = self.run_hook("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(self.repo / "a.py"), "content": "x = 1\n"},
        })
        self.assertNotIn("gate failed", proc.stdout + proc.stderr)

    def test_the_card_itself_can_still_move(self):
        _claimed, error = plan.claim(self.ctx(), "0002", "s1", "main")
        self.assertEqual("", error)
        self.assertEqual(plan.DOING, plan.find(self.ctx(), "0002").state)
