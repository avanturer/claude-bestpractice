"""Parking a task for another session, and taking over the workaround that preceded it."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase

from claude_bestpractice import migrate, plan, store


class TestAHandoffIsRefusedUntilItIsOne(RepoCase):
    """A parked task is read by a session that was not in the room.

    It has the title and nothing else — not the reasoning, not the files, not what was
    already ruled out — so a thin one costs its reader the whole rediscovery the parking
    session was trying to save. Refusing is the same trade the evidence gate makes: a
    moment now against an hour later.
    """

    def test_no_files_is_not_a_handoff(self):
        self.assertIn("no files named", " ".join(plan.handoff_problems([], "x" * 200)))

    def test_a_thin_note_is_not_a_handoff(self):
        problems = " ".join(plan.handoff_problems(["a.py"], "потом доделать"))
        self.assertIn("under", problems)

    def test_files_plus_substance_is(self):
        self.assertEqual([], plan.handoff_problems(["a.py"], "x" * 120))

    def test_the_cli_refuses_and_says_how(self):
        proc = self.plan("park", "Пересобрать словарь", "--note", "потом")
        self.assertEqual(1, proc.returncode)
        self.assertIn("no files named", proc.stderr)
        self.assertIn("--paths", proc.stderr)

    def plan(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
        )


class TestAParkedTaskCarriesItsContext(RepoCase):
    def park(self):
        self.write("src/dictionary.py", "x = 1\n")
        self.write("docs/algorithm.md", "how\n")
        return plan.park(
            self.ctx(),
            "Пересобрать словарь под новый скоринг",
            body="Собран под старую формулу. Пересчёт весов на лету уже пробовали.",
            paths=["src/dictionary.py", "docs/algorithm.md"],
        )

    def test_the_files_survive_a_round_trip(self):
        parked = self.park()
        reloaded = plan.find(self.ctx(), parked.id)
        self.assertEqual(["src/dictionary.py", "docs/algorithm.md"], reloaded.paths)

    def test_the_next_session_gets_everything_in_one_read(self):
        rendered = plan.show(plan.find(self.ctx(), self.park().id))
        self.assertIn("src/dictionary.py", rendered)
        self.assertIn("docs/algorithm.md", rendered)
        self.assertIn("уже пробовали", rendered)

    def test_the_handoff_is_not_on_the_board(self):
        """The board is injected into every session; a full handoff is wanted by one.

        Putting it in front of the other seven is how a context budget dies.
        """
        self.park()
        board = plan.render_for_board(self.ctx())
        self.assertIn("Пересобрать словарь", board)
        self.assertNotIn("уже пробовали", board)
        self.assertNotIn("src/dictionary.py", board)

    def test_a_task_written_before_this_field_still_loads(self):
        """Absent must read as "none named", never as a load failure."""
        old = plan.add(self.ctx(), "older task", body="from a previous version")
        text = old.path.read_text(encoding="utf-8").replace("paths: \n", "")
        old.path.write_text(text, encoding="utf-8")
        reloaded = plan.find(self.ctx(), old.id)
        self.assertIsNotNone(reloaded)
        self.assertEqual([], reloaded.paths)


class TestTheWorkaroundIsTakenOver(RepoCase):
    """Once the ledger can park a task, a hand-written TODO is a second task system.

    Two systems is worse than either, because neither is trusted and both are half-read.
    """

    NOTE = (
        "# Пересобрать словарь под новый скоринг\n\n"
        "Словарь в src/dictionary.py собран под старую формулу.\n"
        "Уточнения в docs/algorithm.md.\n"
    )

    def seed(self, relpath: str = "docs/scoring/TODO-dictionary-realign.md"):
        self.write("src/dictionary.py", "x = 1\n")
        self.write("docs/algorithm.md", "how\n")
        return self.write(relpath, self.NOTE)

    def test_a_hand_written_todo_is_found(self):
        self.seed()
        found = migrate.parked_by_hand(self.ctx())
        self.assertEqual(1, len(found))
        self.assertTrue(found[0].name.startswith("TODO-"))

    def test_a_curated_todo_is_left_alone(self):
        """A bare `TODO.md` is a document a project maintains on purpose. Adopting it
        would be taking over something that was never a workaround."""
        self.write("TODO.md", "- ship the thing\n")
        self.assertEqual([], migrate.parked_by_hand(self.ctx()))

    def test_adoption_carries_the_files_the_note_mentions(self):
        self.seed()
        task_id = migrate.adopt(self.ctx(), migrate.parked_by_hand(self.ctx())[0])
        task = plan.find(self.ctx(), task_id)
        self.assertIn("src/dictionary.py", task.paths)
        self.assertIn("docs/algorithm.md", task.paths)

    def test_a_file_the_note_invents_is_not_carried(self):
        """A hand-written TODO is prose, and prose is full of things that look like
        filenames. Keeping the ones that resolve is what makes it a file list."""
        self.seed()
        path = self.repo / "docs/scoring/TODO-dictionary-realign.md"
        path.write_text(self.NOTE + "\nAlso see nowhere/ghost.py.\n", encoding="utf-8")
        task_id = migrate.adopt(self.ctx(), path)
        self.assertNotIn("nowhere/ghost.py", plan.find(self.ctx(), task_id).paths)

    def test_the_original_becomes_a_pointer_rather_than_a_hole(self):
        """Deleting it would break every link to it. Git keeps the text either way."""
        original = self.seed()
        task_id = migrate.adopt(self.ctx(), original)
        left = original.read_text(encoding="utf-8")
        self.assertTrue(original.exists())
        self.assertIn(migrate.POINTER, left)
        self.assertIn(task_id, left)

    def test_adopting_twice_does_not_file_it_twice(self):
        """Without recognising its own pointer, a second run adopts that, and a third
        adopts the pointer it left — one task per invocation, forever."""
        self.seed()
        migrate.adopt(self.ctx(), migrate.parked_by_hand(self.ctx())[0])
        self.assertEqual([], migrate.parked_by_hand(self.ctx()))
        self.assertEqual(1, len(plan.load_all(self.ctx(), plan.NEXT)))

    def test_the_founder_is_told_it_is_there(self):
        self.assertEqual("", migrate.line(self.ctx()))
        self.seed()
        self.assertIn("outside the work ledger", migrate.line(self.ctx()))

    def test_nothing_is_adopted_without_being_asked(self):
        """Adoption rewrites files in the founder's repository. A plugin that edits
        `docs/` on its own initiative during an upgrade is one nobody installs twice."""
        original = self.seed()
        migrate.repair(self.ctx())
        self.assertEqual(self.NOTE, original.read_text(encoding="utf-8"))


class TestRepairsRunThemselvesAndRunOnce(RepoCase):
    def test_a_repair_is_recorded_and_not_repeated(self):
        first = migrate.repair(self.ctx())
        self.assertEqual([], migrate.pending(self.ctx()), first)
        self.assertEqual([], migrate.repair(self.ctx()))

    def test_unreadable_committed_state_is_set_aside(self):
        broken = store.tier_a(self.ctx(), "half-written.json")
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text('{"half', encoding="utf-8")

        migrate.repair(self.ctx())
        self.assertFalse(broken.exists())
        self.assertTrue(broken.with_suffix(".json.broken").exists(), "the original was deleted")

    def test_readable_state_is_untouched(self):
        good = store.tier_a(self.ctx(), "fine.json")
        good.parent.mkdir(parents=True, exist_ok=True)
        good.write_text(json.dumps({"ok": True}), encoding="utf-8")

        migrate.repair(self.ctx())
        self.assertTrue(good.exists())

    def test_a_task_from_before_the_field_is_backfilled(self):
        task = plan.add(self.ctx(), "older task", body="detail")
        task.path.write_text(
            task.path.read_text(encoding="utf-8").replace("paths: \n", ""), encoding="utf-8"
        )
        migrate.repair(self.ctx())
        self.assertIn("paths:", task.path.read_text(encoding="utf-8"))

    def test_a_failing_repair_does_not_brick_the_session(self):
        """An upgrade that dies halfway leaves the repository worse than the defect."""
        original = dict(migrate._REPAIRS)
        migrate._REPAIRS["9999-explodes"] = lambda ctx: 1 / 0
        try:
            migrate.repair(self.ctx())
        finally:
            migrate._REPAIRS.clear()
            migrate._REPAIRS.update(original)


if __name__ == "__main__":
    unittest.main()
