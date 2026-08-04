"""Open items — the board's list of what is outstanding."""

from __future__ import annotations

import time
import unittest

from helpers import RepoCase

from claude_bestpractice import board, store


class TestAnItemSeenTwiceIsOneItem(RepoCase):
    """`add_open_item` appended unconditionally, so a standing finding piled up.

    Measured on a live repository: `open-items.jsonl` held 70 entries and 4 distinct
    texts, one review finding stored 34 times and another 31. The caller's `item_id`
    carries a timestamp, so identical findings could never collide by construction, and
    a commit-triggered review files one row every time it runs.

    The cost is not untidiness. Each copy is asserted separately to every session that
    reads the board, each has to be retired separately when its subject moves, and the
    four rows that said something new were unfindable among the repeats.
    """

    FINDING = "2 review finding(s): sql-interpolation in api/orders.py"

    def add(self, text: str = FINDING, branch: str = "main", paths=("api/orders.py",)):
        board.add_open_item(
            self.ctx(),
            item_id=f"review-abcd1234-{int(time.time() * 1000)}",
            text=text,
            branch=branch,
            session_id="s1",
            subject_paths=list(paths),
        )

    def stored(self) -> list[dict]:
        return list(store.read_jsonl(store.tier_b(self.ctx(), board.OPEN_ITEMS_FILE)))

    def test_the_same_finding_re_reported_stays_one_item(self):
        self.write("api/orders.py", "print('x')\n")
        for _ in range(34):
            self.add()
        items = board.open_items(self.ctx(), branch="main", with_provenance=False)
        self.assertEqual(1, len(items))
        self.assertEqual(34, items[0]["seen"])
        self.assertEqual(1, len({row["id"] for row in self.stored()}))

    def test_a_different_finding_is_still_its_own_item(self):
        self.write("api/orders.py", "print('x')\n")
        self.write("config.py", "print('y')\n")
        self.add()
        self.add(text="1 review finding(s): secret in config.py", paths=("config.py",))
        self.assertEqual(2, len(board.open_items(self.ctx(), with_provenance=False)))

    def test_the_same_words_on_another_branch_are_another_item(self):
        """Two sessions hitting the same defect on two branches are two open items."""
        self.write("api/orders.py", "print('x')\n")
        self.add(branch="main")
        self.add(branch="feat/orders")
        self.assertEqual(2, len(board.open_items(self.ctx(), with_provenance=False)))

    def test_a_closed_item_is_not_resurrected_by_a_later_sighting(self):
        self.write("api/orders.py", "print('x')\n")
        self.add()
        first = board.open_items(self.ctx(), with_provenance=False)[0]["id"]
        board.close_open_item(self.ctx(), first)
        self.assertEqual([], board.open_items(self.ctx(), with_provenance=False))

        self.add()
        reopened = board.open_items(self.ctx(), with_provenance=False)
        self.assertEqual(1, len(reopened))
        self.assertNotEqual(first, reopened[0]["id"], "a closed item must not be re-bumped")
        self.assertEqual(1, reopened[0]["seen"])

    def test_the_board_reports_the_count_instead_of_the_repeats(self):
        self.write("api/orders.py", "print('x')\n")
        for _ in range(34):
            self.add()
        ctx = self.ctx()
        rendered = board.render(ctx, self.session_record("me"), [], reaped=0)
        self.assertIn("seen 34×", rendered)
        self.assertEqual(1, rendered.count("sql-interpolation"))

    def test_a_re_sighting_keeps_the_item_current(self):
        """Age-gating on first sight retired findings the code still has.

        The subject stamp is refreshed too: the claim was just re-derived from what the
        file holds now, so pinning it to the content it was first seen against would
        suppress a finding that is currently, demonstrably true.
        """
        ctx = self.ctx()
        self.write("api/orders.py", "print('x')\n")
        self.add()
        aged = board.OPEN_ITEM_MAX_AGE_SECONDS + 3600
        rows = self.stored()
        rows[0]["created_at"] -= aged
        rows[0]["last_seen_at"] -= aged
        path = store.tier_b(ctx, board.OPEN_ITEMS_FILE)
        path.write_text("", encoding="utf-8")
        store.append_jsonl(path, rows[0])
        self.assertEqual([], board.open_items(ctx, with_provenance=False))

        self.write("api/orders.py", "print('rewritten')\n")
        self.add()
        live = board.open_items(ctx, with_provenance=True)
        self.assertEqual(1, len(live))
        self.assertEqual(2, live[0]["seen"])
        # First-seen is kept as history; it is `last_seen_at` that decides currency.
        self.assertGreaterEqual(time.time() - live[0]["created_at"], aged)
        self.assertLess(time.time() - live[0]["last_seen_at"], 60)


if __name__ == "__main__":
    unittest.main()
