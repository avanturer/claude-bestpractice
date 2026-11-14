"""The always-on budget counts every skill, agent and command the model is shown.

A command was not counted at all, so three of them cost ~90 tokens on every turn while the
budget said the plugin was under its cap. One kept off the list by `disable-model-invocation`
costs nothing until the founder types it.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_budget  # noqa: E402


class TestWhatIsCounted(unittest.TestCase):
    def counted(self, flag: str) -> str:
        with tempfile.TemporaryDirectory() as raw:
            plugin = Path(raw)
            (plugin / "commands").mkdir()
            (plugin / "commands" / "go.md").write_text(
                f"---\ndescription: one two three four five\n{flag}---\n\nbody\n")
            printed = io.StringIO()
            with mock.patch.object(check_budget, "PLUGIN", plugin), \
                    mock.patch.object(check_budget, "ROOT", plugin), \
                    contextlib.redirect_stdout(printed):
                check_budget.main()
        return printed.getvalue()

    def test_a_command_the_model_is_shown_is_counted(self):
        self.assertIn("across 1 component(s)", self.counted(""))

    def test_one_kept_off_the_list_costs_nothing(self):
        self.assertIn("~0/400 always-on tokens across 0 component(s)",
                      self.counted("disable-model-invocation: true\n"))


class TestWhatCuratedBringsIsCappedTogether(unittest.TestCase):
    """`curated` brings every pack to every session of every project (decision 0101): each pack
    under its own cap can still add up past what a session should pay for advice."""

    def test_packs_under_their_own_cap_over_the_total_are_refused(self):
        description = " ".join(["word"] * 38)
        printed = io.StringIO()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "plugin").mkdir()
            for pack in ("a", "b", "c"):
                for number in range(4):
                    folder = root / "packs" / pack / "skills" / f"s{number}"
                    folder.mkdir(parents=True)
                    (folder / "SKILL.md").write_text(
                        f"---\nname: s{number}\ndescription: {description}\n---\n\nbody\n")
            with mock.patch.object(check_budget, "PLUGIN", root / "plugin"), \
                    mock.patch.object(check_budget, "ROOT", root), \
                    contextlib.redirect_stdout(printed):
                code = check_budget.main()
        self.assertEqual(1, code)
        self.assertIn("packs together", printed.getvalue())
        self.assertNotIn("pack a:", printed.getvalue())
