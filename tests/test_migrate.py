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


class TestARegistryIsFoundByWhatIsInIt(RepoCase):
    """Filename patterns missed an entire real setup, so the plugin stopped guessing.

    Measured on a live repository: `docs/TODO.md`, `docs/pre-release-todo.md` and
    `.claude/commands/todo.md` — three documents tracking real work, none matching the
    `TODO-<name>.md` shape the plugin was quietly expecting. Nobody had agreed to that
    convention. What a registry looks like INSIDE is not a convention; it is markdown.
    """

    REGISTRY = (
        "# Registry\n\n"
        "- [ ] перемерить лимит MegaMarket\n"
        "- [x] уже сделано\n"
        "- [ ] переписать скоринг словаря\n"
    )

    def test_a_document_the_old_pattern_missed_is_found(self):
        self.write("docs/TODO.md", self.REGISTRY)
        found = [p.name for p in migrate.registries(self.ctx())]
        self.assertEqual(["TODO.md"], found)

    def test_every_checkbox_style_counts(self):
        text = "- [ ] dash\n* [ ] star\n+ [ ] plus\n1. [ ] numbered\n2) [ ] paren\n"
        self.assertEqual(5, len(migrate.open_items(text)))

    def test_finished_items_are_not_outstanding(self):
        self.assertEqual(["left"], migrate.open_items("- [x] done\n- [ ] left\n"))

    def test_prose_with_one_stray_checkbox_is_not_a_registry(self):
        self.write("docs/design.md", "Some prose.\n\n- [ ] maybe one day\n")
        self.assertEqual([], migrate.registries(self.ctx()))

    def test_a_github_template_is_a_form_not_a_backlog(self):
        """Its checkboxes are ticked in the pull request body, never in the file.

        So `.github/pull_request_template.md` sat at "3 open item(s)" permanently and
        surfaced on every run, with no migration able to change the count — issue #63.
        Unlike the two conventions this feature invented and retracted, these paths are
        GitHub's own and documented.
        """
        form = "- [ ] `pytest` passes\n- [ ] smoke test\n- [ ] types clean\n"
        for template in (
            ".github/pull_request_template.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE/feature.md",
            ".github/ISSUE_TEMPLATE/bug.md",
            ".github/issue_template.md",
            "docs/pull_request_template.md",
            "PULL_REQUEST_TEMPLATE.md",
        ):
            with self.subTest(template=template):
                self.write(template, form)
                found = [p.relative_to(self.repo).as_posix()
                         for p in migrate.registries(self.ctx())]
                self.assertNotIn(template, found)

    def test_an_ordinary_github_document_is_still_in_scope(self):
        """Only templates are skipped, not everything under `.github/`."""
        self.write(".github/release-checklist.md", "- [ ] tag it\n- [ ] announce it\n")
        found = [p.name for p in migrate.registries(self.ctx())]
        self.assertEqual(["release-checklist.md"], found)

    def test_the_plugins_own_directory_is_not_searched(self):
        """A slash-command describing a TODO workflow is not a backlog."""
        self.write(".claude/commands/todo.md", self.REGISTRY)
        self.assertEqual([], migrate.registries(self.ctx()))


class TestMigrationIsDelegatedAndThenCounted(RepoCase):
    """The plugin cannot read prose, and a model can. So it hands the job over — and
    keeps the verification, which is what makes this delegation rather than persuasion.
    """

    def seed(self) -> None:
        self.write("backend/scoring/dictionary.py", "x = 1\n")
        self.write("docs/TODO.md", TestARegistryIsFoundByWhatIsInIt.REGISTRY)

    def test_the_brief_names_the_items_and_the_check_that_closes_it(self):
        self.seed()
        brief = migrate.brief(self.ctx(), self.repo / "docs/TODO.md")
        self.assertIn("перемерить лимит MegaMarket", brief)
        self.assertNotIn("уже сделано", brief, "a finished item is not work to migrate")
        self.assertIn("claude-bp-plan park", brief)
        self.assertIn("adopt --check", brief)
        self.assertIn("--ignore", brief)

    def test_coverage_counts_what_landed_rather_than_trusting_it(self):
        self.seed()
        target = self.repo / "docs/TODO.md"
        self.assertEqual((2, 0), migrate.coverage(self.ctx(), target))

        plan.park(
            self.ctx(), "перемерить лимит MegaMarket",
            body="Лимит зашит константой из старого прайса. На проде ловили обрезание.",
            paths=["backend/scoring/dictionary.py"], source="docs/TODO.md",
        )
        self.assertEqual((2, 1), migrate.coverage(self.ctx(), target))

    def test_a_task_parked_from_elsewhere_does_not_count(self):
        """Otherwise any unrelated work would silently close out a registry."""
        self.seed()
        plan.park(self.ctx(), "unrelated", body="x" * 120, paths=["backend/scoring/dictionary.py"])
        self.assertEqual((2, 0), migrate.coverage(self.ctx(), self.repo / "docs/TODO.md"))

    def test_several_registries_are_declared_curated_in_one_command(self):
        """A repository that kept its registries by hand has more than one, and five
        invocations to say one thing is a tax on the decision rather than a record."""
        self.write("docs/a.md", "- [ ] one\n- [ ] two\n")
        self.write("docs/b.md", "- [ ] three\n- [ ] four\n")
        self.assertEqual(2, len(migrate.registries(self.ctx())))

        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), "adopt", "--ignore",
             "docs/a.md,docs/b.md"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([], migrate.registries(self.ctx()))

    def test_a_curated_registry_can_be_left_alone_for_good(self):
        """A warning nothing can clear is one the founder learns to scroll past, which
        costs the warnings that matter."""
        self.seed()
        self.assertIn("open item", migrate.line(self.ctx()))

        migrate.ignore(self.ctx(), "docs/TODO.md")
        self.assertEqual([], migrate.registries(self.ctx()))
        self.assertEqual("", migrate.line(self.ctx()))

    def test_the_board_counts_items_not_files(self):
        """"2 documents" says nothing about what is at stake; "31 items" decides it."""
        self.seed()
        self.write("docs/pre-release-todo.md", "".join(f"- [ ] item {i}\n" for i in range(26)))
        self.assertIn("28 open item(s) in 2 checkbox document(s)", migrate.line(self.ctx()))


class TestItNeverClaimsToHaveLookedEverywhere(RepoCase):
    """Twice a shape was invented and quietly expected: a filename, then a checkbox.

    Both were the same mistake — a convention nobody agreed to, presented as detection.
    A registry keyed by id and status matches neither, and is exactly what this feature
    exists for. So detection is best-effort and says so, because the failure that matters
    is not missing a document; it is announcing that nothing was missed.
    """

    REGISTRY = (
        "# Registry\n\n"
        "| ID | Status | Title |\n"
        "|----|--------|-------|\n"
        "| T-001 | planned | перемерить лимит MegaMarket |\n"
        "| T-002 | planned | переписать скоринг словаря |\n"
    )

    def plan(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
        )

    def test_a_registry_without_checkboxes_is_unenumerable(self):
        self.assertTrue(migrate.unenumerable(self.REGISTRY))
        self.assertFalse(migrate.unenumerable("- [ ] one\n- [ ] two\n"))
        self.assertFalse(migrate.unenumerable("- [x] all done\n"), "finished is still countable")

    def test_the_all_clear_names_what_was_looked_for(self):
        """"nothing tracked outside the ledger" is a claim about the repository, and it
        was false in a repository whose primary registry had two planned items."""
        self.write("docs/TODO.md", self.REGISTRY)
        out = self.plan("adopt").stdout
        self.assertNotIn("nothing tracked outside", out)
        # Against the constant, not a phrase: a test pinned to wording breaks on every
        # edit and teaches nothing when it does.
        self.assertIn(migrate.INCOMPLETE, out)

    def test_the_brief_does_not_instruct_over_an_empty_list(self):
        """It printed "tracks 0 open item(s)", no items, then "for each item…"."""
        self.write("docs/TODO.md", self.REGISTRY)
        out = self.plan("adopt", "--brief", "docs/TODO.md").stdout
        self.assertIn("not in a shape this can enumerate", out)
        self.assertNotIn("0 open item(s)", out)
        self.assertIn("Read it yourself", out)

    def test_the_check_refuses_to_report_a_count_it_cannot_know(self):
        """"0 left" on an unreadable format is a green light nobody earned."""
        self.write("docs/TODO.md", self.REGISTRY)
        proc = self.plan("adopt", "--check", "docs/TODO.md")
        self.assertNotIn("left", proc.stdout)
        self.assertIn("unknown", proc.stdout)

    def test_the_caveat_is_on_the_path_where_it_is_believed(self):
        """v1.3.1 printed it only when nothing was found, which is backwards.

        A repository with no checkbox document is one where nobody is mid-task and the
        message is unlikely to be acted on. The MIXED repository is where it is believed,
        because a one-item list reads as a result rather than as an absence — and that
        reading is what produced the field report this feature had to correct.
        """
        self.write("docs/TODO.md", self.REGISTRY)
        self.write("docs/pre-release-todo.md", "- [ ] one\n- [ ] two\n")
        out = self.plan("adopt").stdout
        self.assertIn("docs/pre-release-todo.md", out, "the fixture proves nothing")
        self.assertIn("invisible here", out)

    def test_the_caveat_is_worded_once_so_the_two_paths_cannot_drift(self):
        self.write("docs/TODO.md", self.REGISTRY)
        empty = self.plan("adopt").stdout
        self.write("docs/pre-release-todo.md", "- [ ] one\n- [ ] two\n")
        mixed = self.plan("adopt").stdout
        self.assertIn(migrate.INCOMPLETE, empty)
        self.assertIn(migrate.INCOMPLETE, mixed)

    def test_the_board_names_its_own_scope(self):
        """The line injected into every session had the same completeness problem, and
        is read far more often than the command. It carries the scope in the words it
        already spends rather than in an extra sentence."""
        self.write("docs/pre-release-todo.md", "- [ ] one\n- [ ] two\n")
        line = migrate.line(self.ctx())
        self.assertIn("checkbox document", line)
        self.assertIn("what it cannot see", line)

    def test_a_countable_document_still_gets_its_arithmetic(self):
        self.write("docs/pre-release-todo.md", "- [ ] one\n- [ ] two\n")
        proc = self.plan("adopt", "--check", "docs/pre-release-todo.md")
        self.assertIn("2 open item(s), 0 in the ledger, 2 left", proc.stdout)
        self.assertEqual(1, proc.returncode)
