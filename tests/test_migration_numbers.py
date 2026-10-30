"""A migration a branch adds under a number the trunk has already given another file.

A founder's clone held ten migration numbers twice over, each pair from two sessions that took
"the next number" from the same trunk (decision 0055).
"""

from __future__ import annotations

from helpers import RepoCase, git

from claude_bestpractice import migration_numbers, pullrequest

FOLDER = "backend/src/app/db/migrations"


class TestAClashIsNamedAtMerge(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(f"{FOLDER}/0187_billing.sql", "select 1;\n")
        self.write(f"{FOLDER}/0186_lavka.sql", "select 1;\n")
        self.commit("migrations so far")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        git(["checkout", "-q", "-b", "feat/feedback"], self.repo)

    def test_a_number_the_trunk_gave_another_file_is_a_clash_with_the_next_free_one(self):
        self.write(f"{FOLDER}/0187_feedback.sql", "select 2;\n")
        self.commit("feedback migration")
        said = migration_numbers.clashes(self.ctx(), "main", "feat/feedback")
        self.assertEqual(1, len(said))
        self.assertIn("0187_feedback.sql takes number 0187", said[0])
        self.assertIn("0187_billing.sql", said[0])
        self.assertIn("rename it to 0188", said[0])

    def test_a_free_number_is_no_clash(self):
        self.write(f"{FOLDER}/0188_feedback.sql", "select 2;\n")
        self.commit("feedback migration")
        self.assertEqual([], migration_numbers.clashes(self.ctx(), "main", "feat/feedback"))

    def test_a_file_that_is_not_a_migration_is_not_asked(self):
        self.write("docs/0187_notes.md", "x\n")
        self.commit("notes")
        self.assertEqual([], migration_numbers.clashes(self.ctx(), "main", "feat/feedback"))

    def test_the_merge_lists_it_among_its_blockers(self):
        self.write(f"{FOLDER}/0187_feedback.sql", "select 2;\n")
        self.commit("feedback migration")
        self.assertTrue(any("takes number 0187" in problem
                            for problem in pullrequest.blockers(self.ctx(), "main")))
