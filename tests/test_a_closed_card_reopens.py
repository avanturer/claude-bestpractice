"""A card closed by mistake goes back to the queue as the same card (#307).

> `claude-bp-plan resume 1245` → `task 1245 is not paused`.
> `claude-bp-plan claim 1245` → `task 1245 is already done`.
> A new task had to be filed for the rest of the work. The history now lives in two places, and
> anything that pointed at the first task (notes, a scheduled reminder) had to be redone by hand.

`done` was a state no command left. Each refusal about a card's state now names the command
that moves it on from there, and `TestNoStateIsADeadEnd` runs every one of them from every
state, so a state added without a way out fails here rather than in a founder's clone.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys

from helpers import BIN, RepoCase, add_origin, git, sid

from claude_bestpractice import inbox, plan, sessions

from test_gates import JUNIT_PASS, GateCase

# Every command that moves a card from one state to another, as a session types it.
TRANSITIONS = {
    "claim": [],
    "pause": ["--blocker", "the founder's answer on scope"],
    "resume": [],
    "done": [],
    "reopen": [],
}

EVERY_STATE = (*plan.STATES, plan.WITHDRAWN)


def founder_env() -> dict[str, str]:
    """A terminal outside every session, with the plugin's commands on PATH as a session has."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    env["PATH"] = f"{BIN}{os.pathsep}{env.get('PATH', '')}"
    return env


class LedgerCase(RepoCase):
    def cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), *args],
                              capture_output=True, text=True, cwd=str(self.repo),
                              env=env or founder_env(), timeout=60)

    def card_in(self, state: str) -> plan.Task:
        """A planned card standing in `state`, put there the way the plugin puts one there."""
        ctx = self.ctx()
        if state == plan.WITHDRAWN:
            opener = f"opener-{plan.next_id(ctx)}"
            task = plan.add(ctx, "статус", source=plan.FROM_THE_FOUNDER, opened_by=opener)
            self.assertEqual([task.id], [t.id for t in plan.withdraw_unplanned(ctx, opener)])
            return plan.withdrawn(ctx, task.id)
        task = plan.add(ctx, f"work that ends {state}", paths=["src/app.py"],
                        done_when="the export writes a csv")
        if state in (plan.DOING, plan.DONE):
            plan.claim(ctx, task.id, "a-session-long-gone", "main")
        if state == plan.PAUSED:
            plan.pause(ctx, task.id, "the founder's answer on scope")
        if state == plan.DONE:
            plan.complete(ctx, task.id)
        return task

    def state_of(self, task_id: str) -> str:
        found = plan.find(self.ctx(), task_id) or plan.withdrawn(self.ctx(), task_id)
        return found.state if found else ""


class TestNoStateIsADeadEnd(LedgerCase):
    """Decision 0020, held for the ledger by running the commands rather than reading them."""

    def test_every_refusal_names_a_command_that_runs_from_that_state(self):
        for state in EVERY_STATE:
            for command, extra in TRANSITIONS.items():
                with self.subTest(state=state, command=command):
                    task = self.card_in(state)
                    proc = self.cli(command, task.id, *extra)
                    if proc.returncode == 0:
                        continue
                    named = [line.strip() for line in proc.stderr.splitlines()
                             if line.startswith("  claude-bp-plan ")]
                    self.assertTrue(named, f"a dead end: {proc.stderr!r}")
                    for line in named:
                        ran = subprocess.run(shlex.split(line), capture_output=True, text=True,
                                             cwd=str(self.repo), env=founder_env(), timeout=60)
                        self.assertEqual(0, ran.returncode, f"{line!r} as printed: {ran.stderr}")

    def test_from_every_state_some_command_moves_the_card_on(self):
        for state in EVERY_STATE:
            with self.subTest(state=state):
                moved = []
                for command, extra in TRANSITIONS.items():
                    task = self.card_in(state)
                    if self.cli(command, task.id, *extra).returncode == 0 \
                            and self.state_of(task.id) != state:
                        moved.append(command)
                self.assertTrue(moved, f"nothing moves a card out of {state}")


class TestAClosedCardReopens(LedgerCase):
    def test_it_returns_to_the_queue_as_the_same_card(self):
        ctx = self.ctx()
        first = plan.add(ctx, "the schema", paths=["db/schema.sql"], done_when="migrated")
        task = plan.add(ctx, "Export invoices as CSV", body="the writer is half done",
                        paths=["src/export.py", "tests/test_export.py"],
                        done_when="the export writes a csv", after=[first.id])
        plan.claim(ctx, task.id, "s1", "feat/export")
        plan.complete(ctx, task.id)

        proc = self.cli("reopen", task.id)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn(f"claude-bp-plan claim {task.id}", proc.stdout)

        back = plan.find(self.ctx(), task.id)
        self.assertEqual((plan.NEXT, task.title), (back.state, back.title))
        self.assertEqual(["src/export.py", "tests/test_export.py"], back.paths)
        self.assertEqual(("the export writes a csv", [first.id]), (back.done_when, back.after))
        self.assertEqual(task.created_at, back.created_at)
        self.assertEqual("", back.owner, "the queue holds unclaimed work")
        self.assertIn("the writer is half done", back.body)
        self.assertRegex(back.body, r"\] reopened from done\.")
        self.assertEqual(1, len(plan.copies(self.ctx(), task.id)), "one card, not two")
        self.assertEqual(0, self.cli("claim", task.id).returncode)

    def test_a_pause_it_went_through_is_not_carried_back(self):
        """A pause's blocker rode `claim` into `doing` and `done` into the finished card, and
        came back as WAITING ON a card in the queue."""
        ctx = self.ctx()
        task = self.card_in(plan.PAUSED)
        plan.claim(ctx, task.id, "s1", "main")
        self.assertEqual("", plan.find(ctx, task.id).blocker, "claimed is not waiting")
        plan.complete(ctx, task.id)
        plan.reopen(ctx, task.id)
        self.assertNotIn("WAITING ON", plan.show(plan.find(ctx, task.id)))

    def test_its_history_survives_on_a_card_at_the_length_a_card_keeps(self):
        """Every move cut the body to its first 2,000 characters, so a line the plugin added to
        a long card — this one, a delivery in part, the agents a pause stopped — went first."""
        ctx = self.ctx()
        handoff = "\n".join(f"what is known, step {i}: " + "x" * 70 for i in range(30))
        self.assertGreater(len(handoff), plan.MAX_BODY_CHARS, "the fixture proves nothing")
        task = plan.add(ctx, "a long one", body=handoff, paths=["src/app.py"], done_when="stated")
        plan.claim(ctx, task.id, "s1", "main")
        plan.complete(ctx, task.id)
        plan.reopen(ctx, task.id)
        for _ in range(3):
            plan.pause(ctx, task.id, "the founder's answer on scope")
            plan.resume(ctx, task.id)

        body = plan.find(ctx, task.id).body
        self.assertTrue(body.startswith("what is known, step 0:"), body[:80])
        self.assertRegex(body, r"\] reopened from done\.$")
        self.assertLessEqual(len(body), plan.MAX_BODY_CHARS)
        self.assertEqual(1, body.count(plan.CUT.strip()))

    def test_a_card_not_closed_is_not_reopened(self):
        task = self.card_in(plan.NEXT)
        _, problem = plan.reopen(self.ctx(), task.id)
        self.assertIn("is in the queue", problem)
        self.assertEqual(plan.NEXT, self.state_of(task.id))

    def test_a_number_nothing_was_filed_under_is_said_to_be_none(self):
        self.assertEqual("no task 0999", plan.reopen(self.ctx(), "0999")[1])

    def test_a_live_siblings_closed_card_is_its_own_to_reopen(self):
        """As `done` and `pause` refuse a live sibling's card: the board is its to change."""
        ctx = self.ctx()
        alpha, beta = sid(self.repo, "alpha"), sid(self.repo, "beta")
        for name in (alpha, beta):
            sessions.register(ctx, self.session_record(name))
        task = self.card_in(plan.NEXT)
        plan.claim(ctx, task.id, alpha, "main")
        plan.complete(ctx, task.id, alpha)

        _, problem = plan.reopen(ctx, task.id, beta)
        self.assertIn("held by live session", problem)
        self.assertEqual(plan.DONE, self.state_of(task.id))
        self.assertEqual("", plan.reopen(ctx, task.id, alpha)[1])
        self.assertEqual(plan.NEXT, self.state_of(task.id))

    def test_whoever_comes_after_it_is_told_it_has_not_landed(self):
        """`complete` tells them it landed; the reopen is the same fact turned back."""
        ctx = self.ctx()
        beta = sid(self.repo, "beta")
        sessions.register(ctx, self.session_record(beta))
        task = self.card_in(plan.DONE)
        later = plan.add(ctx, "the importer", paths=["src/import.py"], done_when="imports",
                         after=[task.id])
        plan.claim(ctx, later.id, beta, "feat/import")

        plan.reopen(ctx, task.id)
        said = [note.get("text", "") for note in inbox.pending(ctx, beta)]
        self.assertTrue(any(f"{task.id} was reopened" in text and later.id in text
                            for text in said), said)
        self.assertEqual([task.id], plan.blockers(ctx, plan.find(ctx, later.id)))


class TestTheUpgradeTakesAPausesBlockerOffWhatLeftThePause(LedgerCase):
    """Repair 0047: cards an older `claim` carried out of `paused` still say WAITING ON."""

    def test_only_a_paused_card_keeps_what_it_waits_on(self):
        from claude_bestpractice import migrate

        ctx = self.ctx()
        waiting = self.card_in(plan.PAUSED)
        carried = self.card_in(plan.DOING)
        text = carried.path.parent.parent.joinpath(plan.DOING, carried.path.name)
        stamp = plan.find(ctx, carried.id).updated_at
        text.write_text(text.read_text(encoding="utf-8").replace(
            "blocker: \n", "blocker: the founder's answer on scope\n"), encoding="utf-8")
        self.assertTrue(plan.find(ctx, carried.id).blocker, "the fixture proves nothing")

        said = "; ".join(migrate.repair(ctx))
        self.assertIn("0047-clear-blockers-off-cards-not-paused: 1 card(s)", said)
        self.assertEqual("", plan.find(ctx, carried.id).blocker)
        self.assertEqual(stamp, plan.find(ctx, carried.id).updated_at, "its time is kept")
        self.assertEqual("the founder's answer on scope", plan.find(ctx, waiting.id).blocker)


class TestAWithdrawnCardIsFoundAndReopened(LedgerCase):
    """Decision 0027 keeps a withdrawn card on disk because a session may have its number;
    told to claim it, that session heard "no task" and nothing more."""

    def test_its_number_says_where_it_went_and_the_way_back(self):
        task = self.card_in(plan.WITHDRAWN)
        proc = self.cli("claim", task.id)
        self.assertEqual(1, proc.returncode)
        self.assertIn("was withdrawn", proc.stderr)
        self.assertIn(f"  claude-bp-plan reopen {task.id}", proc.stderr)
        self.assertIn("was withdrawn", self.cli("show", task.id).stderr)

    def test_reopened_it_is_not_withdrawn_again_at_the_next_stop(self):
        ctx = self.ctx()
        task = self.card_in(plan.WITHDRAWN)
        plan.reopen(ctx, task.id)
        opener = plan.find(ctx, task.id).opened_by
        self.assertEqual([], plan.withdraw_unplanned(ctx, opener))
        self.assertEqual(plan.NEXT, self.state_of(task.id))
        self.assertEqual([], plan.copies(ctx, task.id, (plan.WITHDRAWN,)))


class TestEveryCopyMoves(RepoCase):
    """`done` outranks every other state when copies disagree (#123), so a `done` copy left in
    any tree would close the reopened card again on the next read."""

    def test_reopened_from_one_tree_it_is_reopened_in_all_of_them(self):
        from claude_bestpractice.gitctx import resolve

        task = plan.add(self.ctx(), "починить импортер", paths=["src/app.py"], done_when="stated")
        self.commit("file the task")
        other = self.add_worktree("sibling")
        plan.complete(resolve(other), task.id)
        self.assertEqual(2, len(plan.copies(self.ctx(), task.id)), "the fixture proves nothing")

        plan.reopen(resolve(other), task.id)
        self.assertEqual([plan.NEXT, plan.NEXT], [c.state for c in plan.copies(self.ctx(), task.id)])
        self.assertEqual(plan.NEXT, plan.find(self.ctx(), task.id).state)


class TestWhatItWasReopenedOverClosesNothing(RepoCase):
    """The Stop gate closes a session's cards whose files reached the trunk (decision 0011).
    A card reopened after its work landed is held over exactly that work, so the turn that
    reopened it ended with it closed again."""

    def reopened_over(self, content: str = "def export():\n    return 'half'\n") -> plan.Task:
        self.write("src/export.py", content)
        self.commit("the first half of the export")
        ctx = self.ctx()
        task = plan.add(ctx, "Export invoices", paths=["src/export.py"], done_when="all of it")
        plan.claim(ctx, task.id, "s1", ctx.branch)
        plan.complete(ctx, task.id)
        plan.reopen(ctx, task.id)
        plan.claim(ctx, task.id, "s1", ctx.branch)
        return task

    def test_the_delivery_of_the_work_it_was_reopened_over_leaves_it_in_flight(self):
        task = self.reopened_over()
        self.assertEqual([], plan.settle_delivered(self.ctx(), "s1", ["src/export.py"],
                                                   "the base branch"))
        left = plan.find(self.ctx(), task.id)
        self.assertEqual(plan.DOING, left.state)
        self.assertNotIn("closed on delivery", left.body)

    def test_work_done_after_the_reopen_closes_it(self):
        task = self.reopened_over()
        self.write("src/export.py", "def export():\n    return 'all'\n")
        self.commit("the second half")
        closed = plan.settle_delivered(self.ctx(), "s1", ["src/export.py"], "the base branch")
        self.assertEqual([task.id], [t.id for t in closed])
        self.assertEqual([], plan.find(self.ctx(), task.id).reopened_over,
                         "a closure ends what the reopen said")

    def test_the_same_work_squashed_onto_the_trunk_is_still_that_work(self):
        """Content, not commits: the squash GitHub writes is a commit no reopen saw."""
        ctx = self.ctx()
        git(["checkout", "-q", "-b", "feat/export"], self.repo)
        task = self.reopened_over()
        git(["checkout", "-q", "main"], self.repo)
        git(["merge", "-q", "--squash", "feat/export"], self.repo)
        self.commit("Export invoices (#12)")
        self.assertTrue(plan.untouched_since_reopened(ctx, plan.find(ctx, task.id)))

    def test_the_trunk_it_was_reopened_over_counts_from_any_tree(self):
        """Reopened from the main checkout while the clone's `main` lagged the remote: the
        trunk as last fetched is what a later pull brings in."""
        add_origin(self.repo, self.tmp)
        task = self.reopened_over()
        git(["push", "-q", "origin", "main"], self.repo)
        git(["reset", "-q", "--hard", "HEAD~1"], self.repo)
        self.assertNotEqual(git(["rev-parse", "HEAD"], self.repo),
                            git(["rev-parse", "origin/main"], self.repo))
        ctx = self.ctx()
        plan.complete(ctx, task.id)
        plan.reopen(ctx, task.id)
        git(["merge", "-q", "--ff-only", "origin/main"], self.repo)
        self.assertTrue(plan.untouched_since_reopened(ctx, plan.find(ctx, task.id)))

    def test_a_card_never_reopened_is_judged_as_it_always_was(self):
        self.write("src/export.py", "x = 1\n")
        self.commit("the export")
        ctx = self.ctx()
        task = plan.add(ctx, "Export invoices", paths=["src/export.py"], done_when="all of it")
        plan.claim(ctx, task.id, "s1", ctx.branch)
        self.assertFalse(plan.untouched_since_reopened(ctx, plan.find(ctx, task.id)))
        closed = plan.settle_delivered(ctx, "s1", ["src/export.py"], "the base branch")
        self.assertEqual([task.id], [t.id for t in closed])


class TestTheTurnThatReopensItKeepsItOpen(GateCase):
    """The issue's own sequence through the real hooks: work delivered, the card closed, the
    card reopened and claimed, and the turn ended before the rest of the work began."""

    def plan_cli(self, *args: str) -> subprocess.CompletedProcess:
        """`claude-bp-plan`, run by session s1 the way its shell runs it."""
        env = {**founder_env(), "CLAUDE_CODE_SESSION_ID": "s1"}
        return subprocess.run(["claude-bp-plan", *args], capture_output=True, text=True,
                              cwd=str(self.repo), env=env, timeout=60)

    def test_neither_closed_again_nor_demanded_closed(self):
        self.start()
        task = self.claim_a_task("s1", "feature.py")
        self.write("feature.py", "x = 1\n")
        self.write("junit.xml", JUNIT_PASS)
        self.commit("the first half, delivered")
        self.stop()
        self.assertEqual(plan.DONE, plan.find(self.ctx(), task.id).state, "the fixture")

        for command in ("reopen", "claim"):
            proc = self.plan_cli(command, task.id)
            self.assertEqual(0, proc.returncode, proc.stderr)
        proc = self.stop()

        card = plan.find(self.ctx(), task.id)
        self.assertEqual(plan.DOING, card.state, proc.stderr)
        self.assertEqual(sid(self.repo, "s1"), card.owner)
        self.assertNotIn("still says it is in flight", proc.stderr)
        self.assertEqual(1, len(re.findall("closed on delivery", card.body)))

        self.write("feature.py", "x = 2\n")
        self.write("junit.xml", JUNIT_PASS)
        self.commit("the second half, delivered")
        self.stop()
        self.assertEqual(plan.DONE, plan.find(self.ctx(), task.id).state)
