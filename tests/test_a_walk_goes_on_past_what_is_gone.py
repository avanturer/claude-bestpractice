"""A walk over a tree goes on past a directory removed while it walks.

On Python 3.10 `Path.rglob` raised FileNotFoundError for a directory that a sibling's tree or a
reinstall removed mid-walk, and the session start that ran it ended there, its board never
built (#340). The Pythons this suite runs on walk past it, so the old walk is stood in for by an
`rglob` that raises as 3.10's did: a walk still going through `rglob` is a walk that crashes there.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from helpers import RepoCase, gone_as_it_is_walked

from claude_bestpractice import knowledge, stage, store


def rglob_as_on_python_310():
    return mock.patch.object(Path, "rglob", side_effect=FileNotFoundError(2, "gone mid-walk"))


class TheWalk(RepoCase):
    def test_a_directory_gone_mid_walk_is_passed_by(self):
        self.write("tree/kept.md", "kept\n")
        self.write("tree/gone/notes.md", "gone\n")
        with gone_as_it_is_walked():
            found = [p.name for p in store.files_under(self.repo / "tree")]
        self.assertEqual(["kept.md"], found)

    def test_a_directory_it_skips_is_never_entered(self):
        self.write("app/main.py", "x = 1\n")
        self.write("app/node_modules/pkg/index.js", "x\n")
        entered: list[str] = []
        real = os.scandir

        def scandir(path="."):
            entered.append(str(path))
            return real(path)

        with mock.patch("os.scandir", scandir):
            found = [p.name for p in store.files_under(self.repo / "app", {"node_modules"})]
        self.assertEqual(["main.py"], found)
        self.assertFalse([path for path in entered if "node_modules" in path], entered)


class EveryWalkAHookRunsGoesThroughIt(RepoCase):
    def test_the_stage_probe_reads_the_migrations(self):
        """Run by every session start and, below traction, on every gated tool call."""
        self.write("migrations/0001_users.sql", "CREATE TABLE users (id int);\n")
        with rglob_as_on_python_310():
            signals = stage.probe(self.ctx())
        self.assertTrue(signals.has_user_table, signals.reasons)

    def test_an_anchor_on_a_directory_resolves(self):
        self.write("src/billing/order.py", "class Order:\n    pass\n")
        with rglob_as_on_python_310():
            self.assertTrue(knowledge.anchor_resolves(self.ctx(), "Order @ src/billing"))
            self.assertFalse(knowledge.anchor_resolves(self.ctx(), "Invoice @ src/billing"))

    def test_the_founders_rules_are_listed_and_the_decisions_are_not(self):
        self.write(".claude/rules/style.md", "Write short functions.\n")
        self.write(".claude/rules/team/review.md", "Review in pairs.\n")
        self.write(".claude/rules/decisions/0001-a-rule.md", "---\ntitle: x\n---\n")
        self.write(".claude/rules/notes.txt", "not a rule file\n")
        with rglob_as_on_python_310():
            listed = [p.relative_to(self.repo).as_posix()
                      for p in knowledge.existing_rules(self.ctx())]
        self.assertEqual([".claude/rules/style.md", ".claude/rules/team/review.md"], listed)


if __name__ == "__main__":
    unittest.main()
