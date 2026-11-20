"""A card only a linked tree holds is carried to the main checkout before the tree goes
(decision 0106).

The ledger folder is ignored in every tree and never keeps one (`lost_with`), so `git worktree
remove` took a card that existed nowhere else. Repair 0012 carried such cards from one tree
per clone. Each test of a removal path fails on the code before 2.19.3; the rest hold the
carry to what it may move.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from helpers import RepoCase, git

from claude_bestpractice import migrate, plan, store, worktree


def a_card(root: Path, state: str, number: str, title: str, created: str = "") -> Path:
    """A card file in `root`'s ledger as `plan` writes one; `created=""` for one older than
    `created_at`."""
    slug = "-".join(title.lower().split())
    path = root / store.TIER_A_DIRNAME / plan.PLAN_DIR / state / f"{number}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = f"created_at: {created}\n" if created else ""
    path.write_text(
        f"---\nid: {number}\ntitle: {title}\nstate: {state}\nowner: \nbranch: \n"
        f"paths: src/app.py\nsource: \ndone_when: stated\nblocker: \nafter: \nwith: \n{stamp}"
        "---\n\nwhat the work is\n", encoding="utf-8")
    return path


class CardCase(RepoCase):
    def setUp(self):
        super().setUp()
        worktree.hide(self.ctx())  # the ledger ignored in every tree, as a session start leaves it
        self.tree = self.add_worktree("feat-a")
        self.home = self.repo / store.TIER_A_DIRNAME / plan.PLAN_DIR

    def stray(self, number: str = "0009", created: str = "2026-11-20T10:00:00Z") -> Path:
        return a_card(self.tree, plan.DOING, number, "only this tree has it", created)

    def removal_typed(self):
        return self.run_hook("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": f"git worktree remove {self.tree}"},
        })


class TestARemovalCarriesItHome(CardCase):
    def test_the_plugins_removal_carries_it(self):
        text = self.stray().read_text(encoding="utf-8")
        self.assertIsNotNone(worktree.release_now(self.ctx(), self.tree))
        self.assertFalse(self.tree.exists())
        home = self.home / plan.DOING / "0009-only-this-tree-has-it.md"
        self.assertEqual(text, home.read_text(encoding="utf-8"))

    def test_a_removal_the_session_types_carries_it_first(self):
        self.stray()
        self.removal_typed()
        self.assertTrue((self.home / plan.DOING / "0009-only-this-tree-has-it.md").is_file())
        self.assertFalse(list((self.tree / store.TIER_A_DIRNAME).rglob("0009-*.md")))


class TestACardWhoseNumberAnotherHolds(CardCase):
    """Numbered twice, by `next_id` from a tree before decision 0105: carried, two cards would
    answer to one id, so it stays, and so does its tree."""

    def setUp(self):
        super().setUp()
        a_card(self.repo, plan.NEXT, "0009", "a different card", "2026-11-19T08:00:00Z")
        self.card = self.stray()

    def test_it_is_named_among_what_the_removal_would_lose(self):
        self.assertIn(".claude/claude-bestpractice/plan/doing/0009-only-this-tree-has-it.md",
                      " ".join(worktree.lost_with(self.ctx(), self.tree) or []))

    def test_the_plugins_removal_keeps_the_tree(self):
        self.assertIsNone(worktree.release_now(self.ctx(), self.tree))
        self.assertTrue(self.card.is_file())

    def test_a_removal_the_session_types_is_refused(self):
        proc = self.removal_typed()
        self.assertEqual("deny", self.hook_decision(proc), proc.stdout + proc.stderr)
        self.assertIn("0009-only-this-tree-has-it.md", self.hook_reason(proc))
        self.assertIn(f"cat {self.card}", self.hook_reason(proc))
        self.assertTrue(self.card.is_file())


class TestWhatIsNotCarried(CardCase):
    def test_a_copy_of_a_card_the_main_checkout_has_goes_with_the_tree(self):
        a_card(self.repo, plan.DOING, "0009", "only this tree has it", "2026-11-20T10:00:00Z")
        self.stray()
        self.assertEqual(([], []), plan.strays(self.ctx(), self.tree))
        self.assertEqual([], worktree.lost_with(self.ctx(), self.tree))

    def test_a_card_older_than_created_at_is_told_by_its_title(self):
        a_card(self.repo, plan.NEXT, "0009", "only this tree has it")
        self.stray(created="")
        self.assertEqual(([], []), plan.strays(self.ctx(), self.tree))
        a_card(self.repo, plan.NEXT, "0010", "a different card")
        other = self.stray("0010", created="")
        self.assertEqual(([], [other]), plan.strays(self.ctx(), self.tree))

    def test_a_card_git_tracks_there_is_the_branchs(self):
        card = self.stray("0012")
        git(["add", "-f", str(card)], self.tree)
        git(["commit", "-qm", "a card the branch tracks"], self.tree)
        self.assertEqual(([], []), plan.strays(self.ctx(), self.tree))

    def test_a_ledger_a_claim_holds_moves_nothing_and_says_so(self):
        card = self.stray()
        with store.file_lock(store.tier_b(self.ctx(), plan.CLAIM_LOCK)):
            self.assertEqual((0, [card]), plan.carry_home(self.ctx(), self.tree, wait=0.2))
        self.assertTrue(card.is_file())


class TestTheRepair(CardCase):
    """Repair 0012 ran in one tree per clone. This one walks them all."""

    def test_every_linked_trees_cards_are_carried(self):
        other = self.add_worktree("feat-b")
        self.stray()
        a_card(other, plan.NEXT, "0011", "only the other tree has it", "2026-11-20T11:00:00Z")
        self.assertIn("0051-carry-cards-only-a-tree-holds", migrate.pending(self.ctx()))
        migrate.repair(self.ctx())
        self.assertNotIn("0051-carry-cards-only-a-tree-holds", migrate.pending(self.ctx()))
        self.assertTrue((self.home / plan.DOING / "0009-only-this-tree-has-it.md").is_file())
        self.assertTrue((self.home / plan.NEXT / "0011-only-the-other-tree-has-it.md").is_file())


if __name__ == "__main__":
    unittest.main()
