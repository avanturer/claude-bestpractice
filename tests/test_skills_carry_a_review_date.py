"""A skill says when it was last checked, and one past its recheck date is named (#365).

`landing-not-slop` said glassmorphism "dates a page instantly" a year after Apple made Liquid
Glass the navigation layer of its platforms. The file had not changed; the world had.
"""

from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_bestpractice import skills

TODAY = datetime.date(2026, 11, 14)


def skill_file(folder: Path, name: str, frontmatter: str) -> Path:
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: test\n{frontmatter}---\n\n# Body\n")
    return path


class TestTheFrontmatterIsRead(unittest.TestCase):
    def test_a_key_under_metadata_is_named_through_it(self):
        fields = skills.meta('---\nname: x\nmetadata:\n  reviewed: "2026-11-14"\n'
                             "  recheck-after-days: '120'\n---\nbody")
        self.assertEqual("2026-11-14", fields["metadata.reviewed"])
        self.assertEqual("120", fields["metadata.recheck-after-days"])
        self.assertEqual("x", fields["name"])

    def test_a_block_scalar_is_skipped_not_misread(self):
        fields = skills.meta("---\ndescription: |\n  reviewed: 2020-01-01\nname: y\n---\n")
        self.assertNotIn("description", fields)
        self.assertNotIn("description.reviewed", fields)
        self.assertEqual("y", fields["name"])

    def test_no_frontmatter_is_no_fields(self):
        self.assertEqual({}, skills.meta("# just a heading\n"))


class TestEveryShippedSkillIsDated(unittest.TestCase):
    def test_each_says_when_it_was_checked(self):
        self.assertTrue(skills.ours())
        for skill in skills.ours():
            self.assertEqual([], skills.malformed(skill.path, datetime.date.today()), skill.name)

    def test_the_glass_row_follows_the_platform(self):
        """The row that contradicted Apple's guidance tells functional glass from glass on
        every surface, keeps a solid fallback, and is marked as a verdict about the year."""
        text = (skills.SHIPPED / "landing-not-slop" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("dates a page instantly", text)
        row = next(line for line in text.splitlines() if line.startswith("| Glass on every"))
        self.assertIn("†", row)
        self.assertIn("Reduce Transparency", row)
        self.assertIn("navigation", row)


class TestADateIsHeldToItsFormat(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())

    def test_a_skill_with_no_date_is_refused(self):
        path = skill_file(self.folder, "a", "")
        self.assertTrue(skills.malformed(path, TODAY))

    def test_a_date_nobody_could_have_checked_on_is_refused(self):
        path = skill_file(self.folder, "b", 'metadata:\n  reviewed: "2027-01-01"\n')
        self.assertIn("in the future", skills.malformed(path, TODAY)[0])

    def test_a_recheck_that_is_not_a_number_of_days_is_refused(self):
        path = skill_file(self.folder, "c",
                          'metadata:\n  reviewed: "2026-01-01"\n  recheck-after-days: "soon"\n')
        self.assertTrue(skills.malformed(path, TODAY))

    def test_a_well_dated_skill_passes(self):
        path = skill_file(self.folder, "d",
                          'metadata:\n  reviewed: "2026-01-01"\n  recheck-after-days: "90"\n')
        self.assertEqual([], skills.malformed(path, TODAY))


class TestAStaleSkillIsNamed(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        skill_file(self.folder, "trends", 'metadata:\n  reviewed: "2026-01-01"\n'
                                          '  recheck-after-days: "120"\n')
        skill_file(self.folder, "principles", 'metadata:\n  reviewed: "2020-01-01"\n')
        skill_file(self.folder, "current", 'metadata:\n  reviewed: "2026-11-01"\n'
                                         '  recheck-after-days: "120"\n')
        patched = mock.patch.object(skills, "SHIPPED", self.folder)
        patched.start()
        self.addCleanup(patched.stop)

    def test_only_the_one_past_its_date_is_due(self):
        self.assertEqual(["trends"], [skill.name for skill in skills.overdue(skills.ours(), TODAY)])

    def test_a_skill_on_no_outside_facts_never_ages(self):
        principles = next(skill for skill in skills.ours() if skill.name == "principles")
        self.assertIsNone(principles.due())

    def test_status_names_it_with_its_dates(self):
        said = skills.overdue_line(TODAY)
        self.assertIn("trends (reviewed 2026-01-01, due 2026-05-01)", said)
        self.assertNotIn("principles", said)
        self.assertNotIn("current (", said)

    def test_nothing_is_said_when_nothing_is_due(self):
        self.assertEqual("", skills.overdue_line(datetime.date(2026, 2, 1)))
