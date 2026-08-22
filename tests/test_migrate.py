"""Parking a task for another session, and taking over the workaround that preceded it."""

from __future__ import annotations

import json
import subprocess
import sys
import contextlib
import unittest

from helpers import BIN, RepoCase, git

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

    def test_a_scratch_stand_in_is_absorbed_by_the_upgrade(self):
        """Reversed deliberately, and the reasoning is decision 0005.

        This used to assert that an upgrade adopts nothing, on the grounds that a plugin
        editing `docs/` on its own initiative is one nobody installs twice. That holds for
        a document the founder CURATES — see the test below, which still guards it. It
        does not hold for a file a previous SESSION wrote as a stand-in because the ledger
        could not park a task yet: leaving that behind means the repository keeps its
        workaround forever, since a founder upgrades on top of what was working and the
        fix only ever changed what happened next.

        The original is rewritten to a pointer rather than deleted, so nothing that linked
        to it breaks and git keeps the whole text.
        """
        original = self.seed()
        migrate.repair(self.ctx())
        left = original.read_text(encoding="utf-8")
        self.assertIn(migrate.POINTER, left)
        self.assertEqual(1, len(plan.load_all(self.ctx(), plan.NEXT)))

    def test_a_document_the_founder_curates_is_still_never_touched(self):
        """The half of the old rule that stands: judgement a regex does not have."""
        self.write("docs/pre-release-audit.md", "- [ ] one\n- [ ] two\n")
        migrate.repair(self.ctx())
        self.assertEqual("- [ ] one\n- [ ] two\n",
                         (self.repo / "docs/pre-release-audit.md").read_text())


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
        with only_repair("9999-explodes", 1, lambda ctx: 1 / 0):
            migrate.repair(self.ctx())


@contextlib.contextmanager
def only_repair(name: str, revision: int, step):
    """Replace the repair table for the duration of a test, and put it back."""
    original = dict(migrate._REPAIRS)
    migrate._REPAIRS.clear()
    migrate._REPAIRS[name] = (revision, step)
    try:
        yield
    finally:
        migrate._REPAIRS.clear()
        migrate._REPAIRS.update(original)


class TestOldTreesMoveWhereEnteringNeverAsks(RepoCase):
    """`EnterWorktree` prompts on any path outside `.claude/worktrees/`, unconditionally,
    before permissions are consulted. v1.14.0 changed where NEW trees are made and left the
    existing ones exactly where they were, so every entry into them still asked (#111)."""

    def legacy(self, name: str = "legacy"):
        from claude_bestpractice import worktree

        sibling = self.repo.parent / f"{self.repo.name}-{name}"
        git(["worktree", "add", "-q", str(sibling), "-b", f"feat/{name}"], self.repo)
        worktree.record(self.ctx(), name, str(sibling), f"feat/{name}", True, "old-session")
        return sibling

    def home(self):
        return self.repo / ".claude" / "worktrees"

    def test_a_sibling_tree_is_moved_into_the_no_prompt_zone(self):
        sibling = self.legacy()
        migrate.repair(self.ctx())
        self.assertFalse(sibling.exists())
        self.assertTrue((self.home() / sibling.name).is_dir())

    def test_uncommitted_work_travels_with_it(self):
        """`git worktree move`, not delete-and-recreate. Verified against a dirty tree."""
        sibling = self.legacy()
        (sibling / "wip.py").write_text("unfinished = True\n", encoding="utf-8")
        migrate.repair(self.ctx())
        moved = self.home() / sibling.name
        self.assertEqual("unfinished = True\n", (moved / "wip.py").read_text(encoding="utf-8"))

    def test_the_branch_survives(self):
        sibling = self.legacy()
        migrate.repair(self.ctx())
        branches = git(["branch", "--format=%(refname:short)"], self.repo).split()
        self.assertIn("feat/legacy", branches)

    def test_the_registry_points_at_the_new_place(self):
        """Or the next refusal sends the session back to a path that no longer exists."""
        from claude_bestpractice import worktree

        self.legacy()
        migrate.repair(self.ctx())
        found = worktree.mine(self.ctx(), "old-session")
        self.assertIsNotNone(found)
        self.assertTrue(str(found).startswith(str(self.home())), found)

    def test_a_tree_already_in_the_right_place_is_not_reported_as_moved(self):
        """Asserting only that it survives proves nothing — moving it onto itself would
        pass that too. What must be true is that the repair had nothing to say."""
        from claude_bestpractice import hookio, worktree

        made = worktree.provision(self.ctx(), "a current task",
                                  hookio.compose_session_id("s1", str(self.repo)))
        changed = migrate.repair(self.ctx())
        self.assertTrue(made.is_dir())
        self.assertEqual([], [line for line in changed if "no-prompt-zone" in line])

    def unrecorded(self, name: str = "by-hand"):
        """A worktree git knows about and this plugin does not — made by hand, or by the
        CLI's own `--worktree` flag."""
        sibling = self.repo.parent / f"{self.repo.name}-{name}"
        git(["worktree", "add", "-q", str(sibling), "-b", f"feat/{name}"], self.repo)
        return sibling

    def test_a_tree_the_plugin_never_recorded_is_named(self):
        """Going by our own records alone was the defect: a tree the founder made by hand
        has no record here, so nothing here could see it, and entering it asked for
        authorisation on every session — reported three times before the cause was looked
        for in this function rather than in the CLI's changelog."""
        sibling = self.unrecorded()
        said = " ".join(migrate.repair(self.ctx()))
        self.assertIn(str(sibling), said)
        self.assertIn("asks for approval every time", said)

    def test_a_tree_the_plugin_never_recorded_is_not_moved(self):
        """Seeing it is not licence to move it. An editor or a shell may be sitting in a
        tree this plugin did not make, and `git worktree move` under a running process
        breaks it. Our own trees are different: the registry says who is in them."""
        sibling = self.unrecorded()
        (sibling / "wip.py").write_text("unfinished = True\n", encoding="utf-8")
        migrate.repair(self.ctx())
        self.assertTrue(sibling.is_dir(), "moved a tree that was not ours to move")
        self.assertEqual("unfinished = True\n", (sibling / "wip.py").read_text(encoding="utf-8"))

    def test_our_own_trees_are_still_moved_rather_than_named(self):
        """The narrowing must not turn the repair into a report."""
        sibling = self.legacy()
        said = " ".join(migrate.repair(self.ctx()))
        self.assertFalse(sibling.exists())
        self.assertIn("moved under .claude/worktrees/", said)

    def test_when_the_live_sessions_cannot_be_read_nothing_moves(self):
        """Unknown is not "none are live", and collapsing the two moves a directory out
        from under a session that is working in it. A repair that does that is worse than
        the prompt it came to remove."""
        from unittest import mock

        from claude_bestpractice import sessions

        sibling = self.legacy()
        with mock.patch.object(sessions, "live_sessions", side_effect=OSError("unreadable")):
            migrate.repair(self.ctx())
        self.assertTrue(sibling.is_dir(), "moved trees while blind to who was using them")

    def test_a_tree_a_live_session_is_working_in_is_left_alone(self):
        """Moving a directory out from under a running session breaks it. Our own trees
        were only ever moved when nobody was in them; the same has to hold for theirs."""
        from claude_bestpractice import sessions

        sibling = self.legacy()
        record = self.session_record("live-one")
        record.worktree = str(sibling)
        sessions.register(self.ctx(), record)

        migrate.repair(self.ctx())
        self.assertTrue(sibling.is_dir(), "a tree someone is standing in was moved")


class TestTheCeilingIsTakenBackOutOnUpgrade(RepoCase):
    """`max_tool_calls` defaulted to 2000 and `config.save` writes every key, so the number
    is on disk in every repository that ever saved a config. A fix that only changed the
    default would leave all of them blocked, and the founder upgrades on top of what was
    working."""

    def configured(self, value):
        path = store.tier_a(self.ctx(), "config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"max_tool_calls": value}), encoding="utf-8")
        return path

    def value(self, path):
        return json.loads(path.read_text(encoding="utf-8"))["max_tool_calls"]

    def test_the_number_this_plugin_chose_is_lifted(self):
        path = self.configured(2000)
        migrate.repair(self.ctx())
        self.assertEqual(0, self.value(path))

    def test_a_number_the_founder_chose_is_their_word_and_is_left_alone(self):
        path = self.configured(5000)
        migrate.repair(self.ctx())
        self.assertEqual(5000, self.value(path))

    def test_a_config_without_the_key_does_not_gain_one(self):
        path = store.tier_a(self.ctx(), "config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"require_worktree": False}), encoding="utf-8")
        migrate.repair(self.ctx())
        self.assertNotIn("max_tool_calls", json.loads(path.read_text(encoding="utf-8")))


class TestAnUpgradeReconcilesRatherThanTicksOff(RepoCase):
    """The founder upgrades on top of what was working, several versions at a time. A
    repair recorded as done under code that has since changed is exactly the case a
    name-keyed ledger never revisits — and those are the repositories that need it."""

    def instead(self, name: str, revision: int, step):
        return only_repair(name, revision, step)

    def counter(self):
        runs = []

        def step(ctx):
            runs.append(ctx.worktree_root)
            return f"run {len(runs)}"

        return runs, step

    def test_the_same_revision_runs_once(self):
        runs, step = self.counter()
        with self.instead("0001-thing", 1, step):
            migrate.repair(self.ctx())
            migrate.repair(self.ctx())
        self.assertEqual(1, len(runs))

    def test_a_repair_that_got_better_runs_again(self):
        runs, step = self.counter()
        with self.instead("0001-thing", 1, step):
            migrate.repair(self.ctx())
        with self.instead("0001-thing", 2, step):
            self.assertEqual(["0001-thing"], migrate.pending(self.ctx()))
            migrate.repair(self.ctx())
        self.assertEqual(2, len(runs))

    def test_a_record_from_before_revisions_existed_is_reconciled(self):
        """Every repository installed before this reads as revision 0, so every repair at
        revision 1 or above runs again there. That is the upgrade the founder asked for,
        not an accident of the format."""
        runs, step = self.counter()
        store.write_json(
            store.tier_b(self.ctx(), migrate.LEDGER),
            {"0001-thing": {"at": "2026-01-01T00:00:00Z", "detail": "done"}},
        )
        with self.instead("0001-thing", 1, step):
            self.assertEqual(["0001-thing"], migrate.pending(self.ctx()))
            migrate.repair(self.ctx())
        self.assertEqual(1, len(runs))

    def test_unreadable_bookkeeping_is_not_permission_to_skip_the_repairs(self):
        path = store.tier_b(self.ctx(), migrate.LEDGER)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")
        runs, step = self.counter()
        with self.instead("0001-thing", 1, step):
            migrate.repair(self.ctx())
        self.assertEqual(1, len(runs))

    def test_what_it_repaired_is_said_once_and_only_when_it_did_something(self):
        self.assertEqual("", migrate.repaired_line([]))
        self.assertIn("0001-thing: run 1", migrate.repaired_line(["0001-thing: run 1"]))


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

    def adopt(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), "adopt", *args],
            capture_output=True, text=True, cwd=str(cwd or self.repo), timeout=60,
        )

    def test_the_check_honours_the_decision_the_ignore_recorded(self):
        """Two commands, one repository, opposite answers a minute apart.

        `--ignore` said it would not be raised again and `--check` raised it in the next
        breath, with a non-zero exit a script could act on. Read as "the flag persists
        nothing" (#98), which was the reasonable conclusion: the only way to tell that
        the record HAD been written was to go and read the file yourself.
        """
        self.seed()
        self.assertEqual(1, self.adopt("--check", "docs/TODO.md").returncode)

        self.assertEqual(0, self.adopt("--ignore", "docs/TODO.md").returncode)
        after = self.adopt("--check", "docs/TODO.md")
        self.assertEqual(0, after.returncode, after.stdout + after.stderr)
        self.assertIn("curated by hand", after.stdout)

    def test_a_sibling_worktree_honours_a_decision_it_never_merged(self):
        """The decision was made in one checkout, so only that checkout stopped nagging.

        Tier A lives in the working tree. Three documents were declared curated and every
        session since went on opening with "24 open item(s) in 3 checkbox document(s)" —
        in a product whose stated scene is three to eight worktrees of one repository, the
        founder could not make the message go away from any tree but the one they were
        standing in.
        """
        self.seed()
        # Committed, so the sibling carries the DOCUMENT — otherwise it counts nothing
        # there for a reason that has nothing to do with the decision, and the test holds
        # whether or not the fix is present.
        self.commit("registry")
        sibling = self.add_worktree("side")   # cut BEFORE the decision exists
        migrate.ignore(self.ctx(), "docs/TODO.md")
        record = sibling / store.TIER_A_DIRNAME / migrate.IGNORED
        self.assertFalse(record.exists(), "the fixture proves nothing: the sibling has it too")

        from claude_bestpractice.gitctx import resolve

        self.assertEqual([], migrate.registries(resolve(sibling)))
        self.assertEqual("", migrate.line(resolve(sibling)))

    def test_the_check_names_the_checkout_that_actually_holds_the_decision(self):
        """"Delete that entry" is not followable from a tree that has no such file."""
        self.seed()
        self.commit("registry")
        sibling = self.add_worktree("other")
        migrate.ignore(self.ctx(), "docs/TODO.md")

        proc = self.adopt("--check", "docs/TODO.md", cwd=sibling)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn(str(self.repo / store.TIER_A_DIRNAME / migrate.IGNORED), proc.stdout)

    def test_a_document_that_is_not_there_is_recorded_and_said_so(self):
        """Refusing an absent path deadlocked the one flow that needs this most.

        The write gate refuses to CREATE a registry beside the ledger and names this
        command as the way to say "this one is mine" — and the file does not exist yet
        precisely because the gate just refused it (#103). So the decision is recorded and
        the absence is announced, which is how `park` settled the same question: a typo
        must not read as done, and that is what the note is for.
        """
        self.seed()
        proc = self.adopt("--ignore", "docs/not-yet.md")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("not in the tree yet", proc.stdout)
        self.assertTrue(migrate.is_ignored(self.ctx(), "docs/not-yet.md"))

    def test_the_board_counts_items_not_files(self):
        """"2 documents" says nothing about what is at stake; "31 items" decides it."""
        self.seed()
        self.write("docs/pre-release-todo.md", "".join(f"- [ ] item {i}\n" for i in range(26)))
        self.assertIn("28 open item(s) in 2 checkbox document(s)", migrate.line(self.ctx()))

    def test_the_board_names_the_next_command_not_the_genre(self):
        """A count with nothing that starts anything is a count that gets scrolled past.

        Issue #65: `adopt` on its own was reported every session forever while the
        repository carried the same 66 items. The worktree refusal names the destination
        rather than describing the kind of move to make; this is the same obligation.
        """
        self.seed()
        self.write("docs/pre-release-todo.md", "".join(f"- [ ] item {i}\n" for i in range(26)))
        line = migrate.line(self.ctx())
        # The biggest of the two, because that is the one worth a turn.
        self.assertIn("adopt --brief docs/pre-release-todo.md", line)

    def test_the_board_names_the_way_out_as_well_as_the_way_through(self):
        """One exit is not a choice. A repository that curates its documents on purpose
        has to be able to discharge this line, or it learns to ignore the channel."""
        self.seed()
        self.assertIn("--ignore", migrate.line(self.ctx()))


class TestASecondLedgerIsRefusedWhileItIsStillOneFile(RepoCase):
    """The registry check ran at SessionStart and nowhere else, so it could only report
    documents that already existed. A session that CREATED one was told nothing: the
    duplicate was written, wired into three entry points and committed across two commits
    before a merge conflict with another session's migration made it visible (#103).
    """

    def ledger(self) -> None:
        plan.add(self.ctx(), "a task the ledger already holds")

    def refusal(self, relpath: str, text: str) -> str:
        return migrate.second_ledger(self.ctx(), self.repo / relpath, text)

    REGISTRY = "# TODO\n\n- [ ] recheck the limit\n- [ ] backfill the skus\n"

    def test_a_registry_created_beside_a_populated_ledger_is_refused(self):
        self.ledger()
        refusal = self.refusal("docs/TODO.md", self.REGISTRY)
        self.assertIn("second place to track work", refusal)
        self.assertIn("claude-bp-plan add", refusal, "a refusal must name the way through")
        self.assertIn("adopt --ignore", refusal, "and the way out")

    def test_an_empty_ledger_means_this_may_be_how_the_repo_starts(self):
        """SessionStart already reports registries; refusing the first one is a trap."""
        self.assertEqual("", self.refusal("docs/TODO.md", self.REGISTRY))

    def test_a_registry_that_already_exists_is_never_refused(self):
        """Otherwise migrating one — editing it to add the POINTER — is impossible."""
        self.ledger()
        self.write("docs/TODO.md", self.REGISTRY)
        self.assertEqual("", self.refusal("docs/TODO.md", self.REGISTRY + "- [ ] third\n"))

    def test_prose_with_one_stray_checkbox_is_not_a_registry(self):
        self.ledger()
        self.assertEqual("", self.refusal("docs/notes.md", "# Notes\n\nprose\n\n- [ ] one\n"))

    def test_the_ledgers_own_task_documents_are_not_a_second_ledger(self):
        """Task files are full of checkboxes; refusing them would refuse the ledger."""
        self.ledger()
        self.assertEqual("", self.refusal(
            f"{store.TIER_A_DIRNAME}/plan/next/0009-x.md", "- [ ] step one\n- [ ] step two\n"))

    def test_a_pull_request_template_is_a_form_not_a_backlog(self):
        self.ledger()
        self.assertEqual("", self.refusal(
            ".github/pull_request_template.md", "- [ ] tests\n- [ ] docs\n"))

    def test_a_document_declared_curated_is_the_standing_answer(self):
        """The escape the refusal names has to work for a file that does not exist yet."""
        self.ledger()
        migrate.ignore(self.ctx(), "docs/TODO.md")
        self.assertEqual("", self.refusal("docs/TODO.md", self.REGISTRY))


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


class TestAnUpgradeRepairsTheRepositoryItLandsIn(RepoCase):
    """The founder updates the plugin on top of what was working, so behaving better on a
    fresh repository is not the deliverable — the state in front of the upgrade is the
    state that matters. Decision 0005.
    """

    def test_a_scratch_todo_a_session_wrote_is_absorbed_on_upgrade(self):
        self.write("TODO-importer.md", "# Fix the importer\n\nIt falls over on empty prices.\n")
        self.assertEqual(1, len(migrate.parked_by_hand(self.ctx())))

        migrate.repair(self.ctx())

        self.assertEqual([], migrate.parked_by_hand(self.ctx()),
                         "the duplicate survived the upgrade")
        titles = [t.title for t in plan.load_all(self.ctx())]
        self.assertIn("Fix the importer", titles)
        self.assertIn(migrate.POINTER, (self.repo / "TODO-importer.md").read_text(),
                      "the original must point at the task, not vanish")

    def test_the_repair_runs_once_and_is_silent_afterwards(self):
        self.write("TODO-importer.md", "# Fix the importer\n\nprose\n")
        self.assertTrue(any("absorb" in line for line in migrate.repair(self.ctx())))
        self.assertEqual([], [line for line in migrate.repair(self.ctx()) if "absorb" in line])

    def test_a_curated_document_is_not_absorbed_behind_the_founders_back(self):
        """Deciding what in a curated document is a task needs judgement a regex lacks,
        and rewriting the founder's documents on a hunch is worse than the duplicate."""
        self.write("docs/pre-release-audit.md", "- [ ] one\n- [ ] two\n- [ ] three\n")

        migrate.repair(self.ctx())

        self.assertEqual("- [ ] one\n- [ ] two\n- [ ] three\n",
                         (self.repo / "docs/pre-release-audit.md").read_text())


if __name__ == "__main__":
    unittest.main()


class TestTheWitnessTimeoutIsTakenBackOut(RepoCase):
    """The second ceiling this plugin invented, removed the way the first one was.

    v1.37.0 made it configurable, which read as a fix and was not: the 300 seconds it
    lifted sat inside a 900-second Stop hook budget, so raising it only moved the death of
    the run from our timeout to the harness's, where there is no message at all (#158).
    """

    def config_holding(self, **values) -> Path:
        from claude_bestpractice import config, store

        path = store.tier_a(self.ctx(), config.CONFIG_NAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")
        return path

    def test_a_config_carrying_it_is_repaired(self):
        path = self.config_holding(witness_timeout_seconds=1800, test_command=["make", "test"])
        changed = migrate.repair(self.ctx())

        self.assertNotIn("witness_timeout_seconds", json.loads(path.read_text(encoding="utf-8")))
        self.assertTrue([line for line in changed if "witness_timeout_seconds" in line],
                        "a repair that writes to the founder's tree must say so")

    def test_everything_else_in_the_config_is_left_alone(self):
        path = self.config_holding(witness_timeout_seconds=1800, test_command=["make", "test"])
        migrate.repair(self.ctx())
        self.assertEqual(["make", "test"],
                         json.loads(path.read_text(encoding="utf-8"))["test_command"])



class TestAStatementThatWasOnlyASwitchIsForgotten(RepoCase):
    """The founder's word, taken by the wrong reader and kept as what the session is for.

    `worktree_setup ['bash', 'infra/scripts/worktree_db_init.sh']` cleared every test for
    a statement of work — it is long, and it names a path — so it became the task, and a
    statement is only replaced when the founder says something new. It sat on the board,
    in the branch name, and in every scope-drift refusal (#166).
    """

    def session_saying(self, statement: str) -> str:
        from claude_bestpractice import sessions

        rec = sessions.adopt(self.ctx(), "s1")
        sessions.touch(self.ctx(), "s1", task_statement=statement)
        return rec.session_id

    def statement_now(self) -> str:
        from claude_bestpractice import sessions

        return sessions.get(self.ctx(), "s1").task_statement

    def test_a_session_carrying_one_is_repaired(self):
        self.session_saying("worktree_setup ['bash', 'infra/scripts/worktree_db_init.sh']")
        changed = migrate.repair(self.ctx())

        self.assertEqual("", self.statement_now())
        self.assertTrue([line for line in changed if "task statement" in line],
                        "a repair that rewrites session state must say so")

    def test_a_real_instruction_is_left_alone(self):
        real = "почини экспорт CSV, он падает на пустом наборе"
        self.session_saying(real)
        migrate.repair(self.ctx())
        self.assertEqual(real, self.statement_now())
