"""A sibling's tree caught halfway made is not a clone without trees (decision 0105).

`git worktree add` creates `.git/worktrees/<id>/commondir` before it writes it, and every
`git worktree list` dies with 128 until it does (decision 0104). Read as an empty listing, that
sent a write into a live sibling's tree, dropped a database another tree named, numbered a card
twice and recorded repairs done that had seen one tree. Each test freezes that instant as git
leaves it (`a_tree_halfway_made`). A test of a reader that acted wrongly fails on the code
before 2.19.3; the rest hold a reader to what it did then, now that the listing raises.
"""

from __future__ import annotations

import shutil
import types
import unittest

from helpers import RepoCase, a_tree_halfway_made, gate_module, session_record_for, sid

from claude_bestpractice import (
    attempts, delivery, drafts, gitpolicy, knowledge, migrate, plan, sessions, store, worktree,
)
from claude_bestpractice.gitctx import GitError, resolve


class TestTheMainCheckoutIsKnownWithoutTheListing(RepoCase):
    """`main_checkout` answered with the asking tree when the listing died, and everything
    anchored on it moved into that tree: the ledger, the attempts, where trees are cut."""

    def test_a_linked_tree_still_finds_the_main_checkout(self):
        here = resolve(self.add_worktree("feat-a"))
        a_tree_halfway_made(self.repo)
        self.assertEqual(self.repo.resolve(), worktree.main_checkout(here))
        self.assertEqual(self.repo.resolve() / ".claude" / "worktrees" / "b",
                         worktree.target_for(here, "b"))

    def test_a_card_from_a_linked_tree_is_written_in_the_main_checkout(self):
        tree = self.add_worktree("feat-a")
        a_tree_halfway_made(self.repo)
        task = plan.add(resolve(tree), "filed while a sibling's tree is made", done_when="stated")
        self.assertIn(self.repo.resolve(), task.path.resolve().parents, task.path)
        self.assertNotIn(tree.resolve(), task.path.resolve().parents, task.path)


class TestACardNumber(RepoCase):
    def test_a_card_from_a_linked_tree_takes_the_main_checkouts_next_number(self):
        """The ledger lives in the main checkout, and a linked tree counted only its own."""
        for title in ("the first card on this board", "the second card on this board"):
            plan.add(self.ctx(), title, done_when="stated")
        tree = self.add_worktree("feat-a")
        a_tree_halfway_made(self.repo)
        task = plan.add(resolve(tree), "the third card on this board", done_when="stated")
        self.assertEqual("0003", task.id)


class TestAWriteIntoAnotherTree(RepoCase):
    """`foreign_tree` found no tree to own the path, so nothing owned it and the call went."""

    relax_git_policy = False

    def setUp(self):
        super().setUp()
        self.mine = self.add_worktree("feat-mine")
        self.theirs = self.add_worktree("feat-theirs")
        sessions.register(self.ctx(), session_record_for(resolve(self.theirs), "other-session"))
        a_tree_halfway_made(self.repo)

    def call(self, tool: str, tool_input: dict):
        return self.run_hook("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": tool_input, "cwd": str(self.mine),
        }, cwd=self.mine)

    def assert_refused_unlisted(self, proc):
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)
        reason = self.hook_reason(proc)
        self.assertIn("could not list this clone's trees", reason)
        self.assertIn("sibling/commondir", reason)
        self.assertIn("git worktree list", reason)

    def test_a_write_into_a_live_siblings_tree_is_refused(self):
        self.assert_refused_unlisted(
            self.call("Write", {"file_path": str(self.theirs / "a.py"), "content": "y = 1\n"}))

    def test_a_reset_of_a_live_siblings_tree_is_refused(self):
        self.assert_refused_unlisted(
            self.call("Bash", {"command": f"git -C {self.theirs} reset --hard"}))

    def test_a_write_into_its_own_tree_asks_git_nothing(self):
        proc = self.call("Write", {"file_path": str(self.mine / "a.py"), "content": "y = 1\n"})
        self.assertNotEqual("deny", self.hook_decision(proc), self.hook_reason(proc))


class TestADatabaseAnotherTreeNames(RepoCase):
    def test_it_is_kept_while_the_trees_cannot_be_listed(self):
        url = "postgresql://localhost:5432/shop_feat_a"
        removed = self.add_worktree("feat-a")
        (self.add_worktree("feat-b") / ".env").write_text(f"DATABASE_URL={url}\n")
        self.assertTrue(worktree._points_at(self.ctx(), url, removed))
        a_tree_halfway_made(self.repo)
        self.assertTrue(worktree._points_at(self.ctx(), url, removed),
                        "a database another tree names was free to drop")


class TestTheTreeASessionIsJudgedIn(RepoCase):
    """The Stop gate judges a session standing in the main checkout in the tree made for it."""

    def a_session_with_a_tree(self):
        tree = self.add_worktree("work")
        worktree.record(self.ctx(), "work", str(tree), "work", True, session_id="s1")
        return tree

    def test_it_is_still_its_own_tree(self):
        tree = self.a_session_with_a_tree()
        a_tree_halfway_made(self.repo)
        self.assertEqual(tree.resolve(), worktree.working_context(self.ctx(), "s1").worktree_root)

    def test_a_tree_git_no_longer_has_is_still_not_followed(self):
        self.a_session_with_a_tree()
        shutil.rmtree(self.repo / ".git" / "worktrees" / "work")
        a_tree_halfway_made(self.repo)
        self.assertEqual(self.repo.resolve(),
                         worktree.working_context(self.ctx(), "s1").worktree_root)


class TestASessionsOwnTree(RepoCase):
    """A chat whose tree was made by hand is sent back to it by the card it holds there. Not
    found, it was sent to a second tree, or told the command that makes one."""

    def test_no_second_tree_is_cut_or_named(self):
        tree = self.add_worktree("feat-hand")
        me = sid(self.repo, "s1")
        task = plan.add(self.ctx(), "the work in the tree made by hand", done_when="stated",
                        paths=["src/app.py"])
        self.assertIsNotNone(plan.claim(self.ctx(), task.id, me, "feat-hand")[0])
        self.assertIn(str(tree.resolve()), gitpolicy.worktree_refusal(self.ctx(), "the work", me))

        a_tree_halfway_made(self.repo)
        said = gitpolicy.worktree_refusal(self.ctx(), "the work", me)
        self.assertIn("could not list this clone's trees", said)
        self.assertNotIn("git worktree add -b", said)
        self.assertFalse((self.repo / ".claude" / "worktrees").exists())


class TestANumberKeptPerBranch(RepoCase):
    """Decisions and attempts are committed per branch, so their numbers are counted across
    every tree; this tree's alone is the collision those counts exist to prevent."""

    def test_a_decision_is_not_numbered_from_this_tree_alone(self):
        decisions = self.add_worktree("feat-d") / knowledge.DECISIONS_DIR
        decisions.mkdir(parents=True)
        (decisions / "0007-taken-on-another-branch.md").write_text("x\n")
        self.assertEqual(8, drafts.next_number(self.ctx()))
        a_tree_halfway_made(self.repo)
        with self.assertRaises(GitError):
            drafts.next_number(self.ctx())

    def test_an_attempt_is_not_numbered_from_this_tree_alone(self):
        filed = self.add_worktree("feat-t") / store.TIER_A_DIRNAME / attempts.ATTEMPTS_DIR
        filed.mkdir(parents=True)
        (filed / "0004-taken-on-another-branch.md").write_text("x\n")
        self.assertEqual("0005", attempts.next_id(self.ctx()))
        a_tree_halfway_made(self.repo)
        with self.assertRaises(GitError):
            attempts.record(self.ctx(), "a dead end", "it failed", ["src/app.py"])

    def test_the_finish_is_filed_unverified_all_the_same(self):
        """The attempt is a note for later sessions; the mark is what a merge reads."""
        here = resolve(self.add_worktree("feat-x"))
        a_tree_halfway_made(self.repo)
        gate_module("evidence-gate")._give_up(
            here, session_record_for(here, "s1"), "no run", ["src/app.py"])
        self.assertTrue(delivery.unverified_finishes(here, "feat-x"))


# Repairs that read every tree of the clone, by the step `_REPAIRS` runs and what it reads.
_EVERY_TREE = (
    ("0005", lambda ctx: migrate._trees_that_still_prompt(ctx, worktree.home_of(ctx))),
    ("0025", migrate._carry_checkpoints_out_of_trees),
    ("0046", migrate._carry_attempts_out_of_trees),
    ("0048", migrate._refresh_what_pull_requests_are_blocked_by),
    ("0019 0021 0030 0033 0038 0051", migrate._trees_of),
)


class TestARepairWaitsForTheTrees(RepoCase):
    """A repair that returns is recorded done for good, and these returned having seen this
    tree, or none."""

    def test_no_repair_runs_while_the_trees_cannot_be_listed(self):
        before = migrate.pending(self.ctx())
        halfway = a_tree_halfway_made(self.repo)
        self.assertEqual([], migrate.repair(self.ctx()))
        self.assertEqual(before, migrate.pending(self.ctx()))
        shutil.rmtree(halfway)
        migrate.repair(self.ctx())
        self.assertLess(len(migrate.pending(self.ctx())), len(before))

    def test_a_step_that_reads_every_tree_says_it_could_not(self):
        a_tree_halfway_made(self.repo)
        for repairs, step in _EVERY_TREE:
            with self.subTest(repairs=repairs), self.assertRaises(GitError):
                step(self.ctx())


class TestWhatOnlyGoesUnsaid(RepoCase):
    """The readers a failed listing costs a line, a convenience or a refusal it already gave:
    each still says nothing, rather than crash its gate over the listing that now raises."""

    def test_the_sessions_stay_live(self):
        here = resolve(self.add_worktree("feat-a"))
        sessions.register(here, session_record_for(here, "s1"))
        a_tree_halfway_made(self.repo)
        self.assertEqual(["s1"], [r.session_id for r in sessions.live_sessions(self.ctx())])
        self.assertEqual([], sessions.reap(self.ctx()))

    def test_each_omission_is_still_only_that(self):
        tree = self.add_worktree("feat-a")
        (self.repo / ".env").write_text("DATABASE_URL=postgresql://localhost/shop\n")
        (tree / ".env").write_text("DATABASE_URL=postgresql://localhost/shop\n")
        here = resolve(tree)
        a_tree_halfway_made(self.repo)
        pre_tool, session_start = gate_module("pre-tool"), gate_module("session-start")
        for name, said, quiet in (
            ("the board's shared database", lambda: worktree.shared_database(here), None),
            ("the step into a sibling tree", lambda: gitpolicy._in_a_nested_tree(
                here, tree.resolve(), self.repo.resolve()), False),
            ("a write beside the checkout", lambda: pre_tool._in_other_trees(
                here, tree.resolve(), [self.tmp / "elsewhere" / "a.py"]), []),
            ("EnterWorktree's approval", lambda: pre_tool._listed_tree(here, tree), False),
            ("the move home", lambda: bool(worktree._why_it_cannot_go_home(
                self.ctx(), tree.resolve(), self.repo / "x", "s1")), True),
            ("the entry's cost", lambda: worktree.permission_advice(self.ctx(), "s1"), ""),
            ("the board's tree line", lambda: "worktree: work happens" in session_start
             ._worktree_line(self.ctx(), types.SimpleNamespace(require_worktree=True), "s1"),
             True),
        ):
            with self.subTest(name):
                self.assertEqual(quiet, said())


if __name__ == "__main__":
    unittest.main()
