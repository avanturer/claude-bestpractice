"""Parking a task for another session, and taking over the workaround that preceded it."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import contextlib
import unittest

from helpers import BIN, RepoCase, git

from claude_bestpractice import limits, migrate, plan, store


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

    def test_the_hyphen_is_the_convention_and_the_underscore_is_not(self):
        """`TODO_LIST.md` is somebody's list; the comment above the pattern always said so."""
        self.write("docs/TODO_LIST.md", "# a list\n\nnot a stand-in\n")
        self.assertEqual([], migrate.parked_by_hand(self.ctx()))

    def test_another_repositorys_files_are_not_this_ones(self):
        """A submodule, a nested repository and ignored vendored code all matched the name,
        and the upgrade rewrote them: ` m vendor/upstream` in the founder's status, and a
        card on the board for somebody else's plan."""
        from helpers import make_repo

        upstream = make_repo(self.tmp, "upstream")
        (upstream / "TODO-v2.md").write_text("# upstream's own plan\n\ntheirs\n", encoding="utf-8")
        git(["add", "-A"], upstream)
        git(["commit", "-qm", "their plan"], upstream)
        git(["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(upstream),
             "vendor/upstream"], self.repo)
        self.write(".gitignore", "scratch/\n")
        self.write("scratch/TODO-generated.md", "# generated\n\nignored\n")
        self.commit("vendor it, and ignore the scratch area")
        nested = self.repo / "third_party" / "libfoo"
        nested.mkdir(parents=True)
        git(["init", "-q"], nested)
        (nested / "TODO-list.md").write_text("# libfoo's own\n\ntheirs\n", encoding="utf-8")

        self.assertEqual([], migrate.parked_by_hand(self.ctx()))
        migrate.repair(self.ctx())
        self.assertEqual("", git(["status", "--porcelain", "--", "vendor"], self.repo))
        self.assertEqual("# libfoo's own\n\ntheirs\n",
                         (nested / "TODO-list.md").read_text(encoding="utf-8"))
        self.assertEqual([], plan.load_all(self.ctx()))

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

    def test_the_founders_config_is_never_set_aside(self):
        """It is theirs and committed, a file they edit by hand: moving it aside to fix a
        parse error left `git status` showing their config deleted, and every gate on the
        defaults in silence."""
        config = store.tier_a(self.ctx(), "config.json")
        config.write_text('{"enabled": false,}', encoding="utf-8")
        self.commit("the founder's config")

        migrate.repair(self.ctx())
        self.assertTrue(config.is_file())
        self.assertFalse(config.with_suffix(".json.broken").exists())

    def test_a_config_an_earlier_upgrade_set_aside_is_put_back(self):
        config = store.tier_a(self.ctx(), "config.json")
        config.replace(config.with_suffix(".json.broken"))

        changed = migrate.repair(self.ctx())
        self.assertEqual({"require_worktree": False, "protect_trunk": False},
                         json.loads(config.read_text(encoding="utf-8")))
        self.assertFalse(config.with_suffix(".json.broken").exists())
        self.assertTrue([line for line in changed if "config.json" in line], changed)

    def test_it_is_put_back_in_whichever_tree_it_was_set_aside_in(self):
        """The quarantine ran in the tree that happened to start first; this runs once per
        clone, so it cannot wait for that tree to start again."""
        tree = self.add_worktree("elsewhere")
        config = tree / store.TIER_A_DIRNAME / "config.json"
        config.replace(config.with_suffix(".json.broken"))

        migrate.repair(self.ctx())
        self.assertTrue(config.is_file())
        self.assertEqual("", git(["status", "--porcelain"], tree))

    def test_one_the_founder_has_written_since_is_left_alone(self):
        config = store.tier_a(self.ctx(), "config.json")
        broken = config.with_suffix(".json.broken")
        broken.write_text('{"old": true,}', encoding="utf-8")

        migrate.repair(self.ctx())
        self.assertEqual({"require_worktree": False, "protect_trunk": False},
                         json.loads(config.read_text(encoding="utf-8")))
        self.assertTrue(broken.exists())

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

    def test_the_compaction_demand_s_marker_is_dropped(self):
        """The roll of sessions the old `PreCompact` block had already interrupted. That
        block is gone — it cancelled the founder's `/compact` and reached the model never
        — and the founder upgrades on top of the clone that ran it."""
        stale = store.tier_b(self.ctx(), "compaction-notes-demanded.json")
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text(json.dumps(["s1-abcd1234"]), encoding="utf-8")

        migrate.repair(self.ctx())
        self.assertFalse(stale.exists())

    def test_a_failing_repair_does_not_brick_the_session(self):
        """An upgrade that dies halfway leaves the repository worse than the defect."""
        with only_repair("9999-explodes", 1, lambda ctx: 1 / 0):
            migrate.repair(self.ctx())

    def test_sessions_that_start_together_run_each_repair_once(self):
        """Restarting sessions after an upgrade starts them together, and each ran every
        pending repair: in five trials out of five one scratch TODO became two to four cards
        on the one board."""
        self.write("TODO-refactor-parser.md",
                   "# Refactor the parser\n\nThe tokenizer in a.py double-counts newlines.\n")
        self.commit("a scratch todo from an older session")
        starts = [
            subprocess.Popen([sys.executable, str(BIN / "session-start")], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=str(self.repo), text=True)
            for _ in range(4)
        ]
        for index, proc in enumerate(starts):
            proc.stdin.write(json.dumps({"session_id": f"s{index}", "cwd": str(self.repo),
                                         "hook_event_name": "SessionStart", "source": "startup"}))
            proc.stdin.close()
        for proc in starts:
            proc.wait(timeout=180)

        self.assertEqual(["Refactor the parser"], [t.title for t in plan.load_all(self.ctx())])

    def test_a_session_that_cannot_have_the_lock_starts_without_them(self):
        """Its sibling is running them. Waiting its whole start out, or running them beside
        the sibling, is what the lock is for."""
        from unittest import mock

        ctx = self.ctx()
        with mock.patch.object(migrate, "_LOCK_TIMEOUT", 0.1):
            with store.file_lock(store.tier_b(ctx, migrate.REPAIR_LOCK)):
                self.assertEqual([], migrate.repair(ctx))
        self.assertEqual(len(migrate._REPAIRS), len(migrate.pending(ctx)))

    def board(self) -> str:
        proc = self.run_hook("session-start", {
            "session_id": "s1", "hook_event_name": "SessionStart", "source": "startup",
        })
        payload = json.loads(proc.stdout or "{}")
        return payload.get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_a_revision_that_is_not_a_number_does_not_take_the_board_with_it(self):
        """`repair` promises never to raise, and `_ran_at` sat outside its guard: one
        revision that was not a number raised, and the session started with no board."""
        store.write_json(store.tier_b(self.ctx(), migrate.LEDGER),
                         {"0001-task-paths": {"revision": "one"}})
        self.assertIn("OTHER LIVE SESSIONS", self.board())
        self.assertEqual([], migrate.pending(self.ctx()),
                         "a garbled record is run again and written properly")

    def test_bookkeeping_that_cannot_be_written_stops_no_repair(self):
        """An unrecorded step costs a re-run that finds nothing to do. A raising `_mark`
        cost every repair after the first, on every start, for as long as it lasted."""
        store.tier_b(self.ctx(), migrate.LEDGER).mkdir(parents=True)
        config = store.tier_a(self.ctx(), "config.json")
        config.replace(config.with_suffix(".json.broken"))

        self.assertIn("OTHER LIVE SESSIONS", self.board())
        self.assertTrue(config.is_file(), "the repairs after the first never ran")

    def test_an_inbox_an_interrupted_reindex_stranded_is_put_back(self):
        """A reindex that raised between its purge and its put-back left the queued notes
        in the carry directory beside Tier B, where nothing reads them."""
        ctx = self.ctx()
        stranded = store.tier_b(ctx).parent / f".{store.TIER_B_DIRNAME}.carry" / "inbox"
        store.write_json(stranded / "peer.json", [{"text": "queued before the crash"}])

        changed = migrate.repair(ctx)

        self.assertEqual([{"text": "queued before the crash"}],
                         store.read_json(store.tier_b(ctx, "inbox", "peer.json")))
        self.assertTrue([line for line in changed if "claude-bp-reindex" in line], changed)


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

    def test_the_copy_every_tree_reads_is_the_one_lifted_from_any_tree(self):
        """Revision 1 repaired whichever tree started first. Every tree reads the main
        checkout's copy now, so a 2000 left there would bind every worktree again."""
        from claude_bestpractice.gitctx import resolve

        path = self.configured(2000)
        store.write_json(store.tier_b(self.ctx(), migrate.LEDGER),
                         {"0004-lift-the-tool-call-ceiling": {"revision": 1}})
        tree = self.add_worktree("elsewhere")

        migrate.repair(resolve(tree))
        self.assertEqual(0, self.value(path))

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


class TestTwoStoresThatFilledWithRepeats(RepoCase):
    """Both were append-only logs nobody could act on: the decision inbox had sixty rows
    carrying four sentences while `claude-bp status` pointed at it as the next action, and
    the defect store had ninety-four rows of this plugin's own test fixture behind a
    command whose job is to file them at GitHub."""

    def inbox_holding(self, *quotes: str) -> Path:
        from claude_bestpractice import drafts, store

        path = store.tier_b(self.ctx(), drafts.INBOX_FILE)
        for quote in quotes:
            store.append_jsonl(path, {"marker": "constraint", "quote": quote,
                                      "branch": "main", "session_id": "s1",
                                      "created_at": 1.0, "subject_paths": []})
        return path

    def defects_holding(self, *gates: str) -> Path:
        from claude_bestpractice import defects, store

        path = store.tier_b(self.ctx(), defects.DEFECTS_FILE)
        for number, gate in enumerate(gates):
            store.append_jsonl(path, {"signature": f"sig{number}", "gate": gate,
                                      "error": "RuntimeError: kaboom", "where": "x.py:1",
                                      "seen": 1, "sent_at": 0.0})
        return path

    def test_the_inbox_keeps_one_row_per_draft(self):
        from claude_bestpractice import store

        path = self.inbox_holding("keep the gate on", "keep the gate on", "never touch prod")
        changed = migrate.repair(self.ctx())

        quotes = [row.get("quote") for row in store.read_jsonl(path)]
        self.assertEqual({"keep the gate on", "never touch prod"}, set(quotes))
        self.assertEqual(2, len(quotes), "the duplicate survived the collapse")
        self.assertTrue([line for line in changed if "decision draft" in line])

    def test_a_draft_the_founder_already_handled_stays_handled(self):
        """Resolution is recorded BY QUOTE, in the same log. A collapse that keeps only
        the pending rows throws it away — and the extractor re-reads the same turns every
        run, so every draft the founder discarded would be back within the hour."""
        from claude_bestpractice import drafts

        self.inbox_holding("keep the gate on", "keep the gate on")
        drafts.resolve(self.ctx(), "keep the gate on")
        migrate.repair(self.ctx())

        # The founder says it again, which is what the extractor does on every run.
        drafts.record(self.ctx(), [drafts.Draft("constraint", "keep the gate on", "main",
                                                "s1", 2.0, [])])
        self.assertEqual([], drafts.pending(self.ctx()),
                         "a draft the founder discarded came back after the collapse")

    def unverified_holding(self, *reasons: str) -> "Path":
        from claude_bestpractice import store

        path = store.tier_b(self.ctx(), "unverified.jsonl")
        for number, reason in enumerate(reasons):
            store.append_jsonl(path, {"session_id": f"s{number}", "branch": "feat/a",
                                      "baseline_commit": "abc", "reason": reason,
                                      "recorded_at": 1.0})
        return path

    def test_a_failure_marker_carrying_a_whole_base64_blob_is_trimmed(self):
        """Append-only, clone-wide, and re-parsed by every pull-request check.

        Until 1.54.0 the gate's reason was bounded at 25 lines and nothing else, so a run
        that failed with one enormous line wrote that line here permanently.
        """
        from claude_bestpractice import store

        path = self.unverified_holding("the suite FAILS. " + "A" * 500_000, "short one")
        changed = migrate.repair(self.ctx())

        rows = store.read_jsonl(path)
        self.assertEqual(2, len(rows), "a row was dropped rather than trimmed")
        self.assertLessEqual(max(len(r["reason"]) for r in rows), 2000)
        self.assertEqual("short one", rows[1]["reason"], "a small reason was rewritten")
        self.assertTrue([line for line in changed if "unverified-finish" in line])

    def test_which_branch_finished_unverified_survives_the_trim(self):
        """The only field any reader looks at. Losing it forgives a finish nobody proved."""
        from claude_bestpractice import delivery

        self.unverified_holding("x" * 100_000)
        migrate.repair(self.ctx())
        self.assertTrue(delivery.unverified_on(self.ctx(), "feat/a"))

    def test_markers_already_within_the_cap_are_left_alone(self):
        self.unverified_holding("short", "also short")
        self.assertEqual([], [line for line in migrate.repair(self.ctx())
                              if "unverified-finish" in line])

    def test_crashes_from_things_the_plugin_does_not_ship_are_dropped(self):
        from claude_bestpractice import store

        path = self.defects_holding("__main__.py", "python3 -m unittest", "evidence-gate")
        changed = migrate.repair(self.ctx())

        self.assertEqual(["evidence-gate"], [row.get("gate") for row in store.read_jsonl(path)])
        self.assertTrue([line for line in changed if "does not ship" in line])


class TestCardsLeftInFlightByTheMissingClosingHalf(RepoCase):
    """Every repository that has been running this plugin carries rows saying work is in
    flight over work that shipped weeks ago — nothing anywhere closed a card, so one left
    `doing` only if somebody remembered a command. The new rule closes them going forward
    and reaches nothing already on the board, which is the half an upgrade owes.
    """

    def shipped(self, *relpaths: str) -> None:
        for rel in relpaths:
            self.write(rel, "x = 1\n")
        self.commit("the work")
        git(["update-ref", "refs/remotes/origin/main", "HEAD"], self.repo)

    def in_flight(self, owner: str, *paths: str):
        ctx = self.ctx()
        task = plan.add(ctx, "work that shipped", paths=list(paths), done_when="stated")
        claimed, error = plan.claim(ctx, task.id, owner, ctx.branch)
        self.assertEqual("", error)
        return claimed

    def test_a_card_over_work_already_on_the_trunk_is_closed(self):
        task = self.in_flight("a-session-that-is-gone", "src/app.py")
        self.shipped("src/app.py")

        changed = migrate.repair(self.ctx())
        self.assertEqual(plan.DONE, plan.find(self.ctx(), task.id).state)
        self.assertTrue([line for line in changed if "already on the trunk" in line])

    def test_a_card_whose_work_sits_unmerged_in_a_sibling_tree_is_left_alone(self):
        """The default workflow: the work is in a worktree, and the tree that happens to run
        the repair — the main checkout — never touched the files, so they equal the trunk
        there. It closed the card with a delivery note over work that never left the sibling."""
        from claude_bestpractice.gitctx import resolve

        self.shipped("src/app.py")
        tree = self.add_worktree("rework")
        task = plan.add(self.ctx(), "rework the app", paths=["src/app.py"], done_when="stated")
        plan.claim(resolve(tree), task.id, "a-session-that-is-gone", "rework")
        (tree / "src" / "app.py").write_text("x = 2  # the rework\n", encoding="utf-8")
        git(["commit", "-qam", "the rework, not merged"], tree)

        migrate.repair(self.ctx())
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_a_card_whose_branch_never_touched_its_files_is_left_alone(self):
        """A branch standing where it was cut holds the trunk's content too."""
        import os
        import subprocess

        self.write("src/app.py", "x = 1\n")
        git(["add", "-A"], self.repo)
        subprocess.run(["git", "commit", "-qm", "long before the card"], cwd=str(self.repo),
                       check=True, env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})
        git(["update-ref", "refs/remotes/origin/main", "HEAD"], self.repo)
        git(["branch", "never-started"], self.repo)
        task = plan.add(self.ctx(), "work never begun", paths=["src/app.py"], done_when="stated")
        plan.claim(self.ctx(), task.id, "a-session-that-is-gone", "never-started")

        migrate.repair(self.ctx())
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_a_squash_merge_is_a_delivery_too(self):
        """Its branch is never an ancestor of the trunk; its content is on it all the same."""
        from claude_bestpractice.gitctx import resolve

        self.shipped("src/app.py")
        tree = self.add_worktree("squashed")
        task = plan.add(self.ctx(), "rework the app", paths=["src/app.py"], done_when="stated")
        plan.claim(resolve(tree), task.id, "a-session-that-is-gone", "squashed")
        (tree / "src" / "app.py").write_text("x = 2  # the rework\n", encoding="utf-8")
        git(["commit", "-qam", "the rework"], tree)
        self.write("src/app.py", "x = 2  # the rework\n")
        self.commit("the rework (squashed)")
        git(["update-ref", "refs/remotes/origin/main", "HEAD"], self.repo)

        migrate.repair(self.ctx())
        self.assertEqual(plan.DONE, plan.find(self.ctx(), task.id).state)

    def test_a_card_a_live_session_is_holding_is_left_alone(self):
        """A card a chat is holding right now is that chat's to close."""
        from claude_bestpractice import sessions

        self.shipped("src/app.py")
        rec = self.session_record("live-one")
        sessions.register(self.ctx(), rec)
        task = self.in_flight(rec.session_id, "src/app.py")

        migrate.repair(self.ctx())
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_a_card_whose_work_has_not_shipped_is_left_alone(self):
        self.shipped("src/app.py")
        self.write("src/app.py", "x = 2  # moved on since the trunk\n")
        self.commit("still ahead of the trunk")
        task = self.in_flight("a-session-that-is-gone", "src/app.py")

        migrate.repair(self.ctx())
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_every_file_the_card_named_has_to_have_shipped(self):
        """Conservative where `settle_delivered` is not: this runs unattended in somebody
        else's repository and is inferring a delivery rather than watching one."""
        self.shipped("src/app.py")
        self.write("src/later.py", "y = 2\n")
        self.commit("not on the trunk")
        task = self.in_flight("a-session-that-is-gone", "src/app.py", "src/later.py")

        migrate.repair(self.ctx())
        self.assertEqual(plan.DOING, plan.find(self.ctx(), task.id).state)

    def test_it_runs_once_and_says_nothing_the_second_time(self):
        self.in_flight("a-session-that-is-gone", "src/app.py")
        self.shipped("src/app.py")

        migrate.repair(self.ctx())
        self.assertEqual([], [line for line in migrate.repair(self.ctx())
                              if "already on the trunk" in line])


class TestRenamesGitWasNeverToldAbout(RepoCase):
    """Fifty unstaged deletions, one per task a pre-1.61.1 transition moved (#208).

    The fix only helps transitions that have not happened yet, and the founder upgrades on
    top of what was already running — so the upgrade owes the checkout it lands in the
    same repair.
    """

    def stranded(self):
        """A committed task file moved the way transitions used to move: on disk only."""
        task = plan.add(self.ctx(), "Do a thing", done_when="stated", paths=["src/app.py"])
        self.commit("ledger")
        target = task.path.parent.parent / plan.PAUSED / task.path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(task.path.read_text(encoding="utf-8"), encoding="utf-8")
        task.path.unlink()
        return task, target

    def test_the_bare_deletion_becomes_a_staged_one_and_nothing_is_added(self):
        """This repair on its own. It restaged these as renames until the ledger left git
        (#219); a rename is the card going back in with the next commit, so the deletion is
        staged instead — but a repair is worth testing for what IT does, or a later change
        to a neighbour silently retires it."""
        task, target = self.stranded()
        migrate._restage_ledger_moves_git_lost(self.ctx())

        staged = git(["diff", "--cached", "--name-status"], self.repo).splitlines()
        self.assertEqual([f"D\t{task.path.relative_to(self.repo).as_posix()}"], staged)
        self.assertTrue(target.is_file(), "the moved card left the disk")

    def test_a_deletion_with_no_counterpart_is_left_alone(self):
        """It may be one somebody meant; a repair that guesses is worse than the defect."""
        task = plan.add(self.ctx(), "Do a thing", done_when="stated", paths=["src/app.py"])
        self.commit("ledger")
        task.path.unlink()

        migrate._restage_ledger_moves_git_lost(self.ctx())
        self.assertEqual("", git(["diff", "--cached", "--name-only"], self.repo))

    def test_the_whole_chain_takes_the_ledger_out_of_git(self):
        """#219, end to end: whatever shape the index was in, the upgrade leaves the cards
        on disk and out of git — one staged deletion for the founder to commit, and nothing
        of the ledger tracked anywhere."""
        _task, target = self.stranded()
        migrate.repair(self.ctx())

        self.assertTrue(target.is_file(), "the card left the disk")
        self.assertEqual("", git(["ls-files", store.TIER_A_DIRNAME + "/plan"], self.repo))
        self.assertIn("D", git(["diff", "--cached", "--name-status"], self.repo))


class TestCarryingATaskHomeIsAMoveInBothIndexes(RepoCase):
    """The carry-home repair recreated the defect the repair after it exists to undo.

    `_carry_this_worktrees_tasks_home` moves a task file out of the worktree and into the
    main checkout. Two worktrees of one clone have two indexes, so that move cannot be one
    rename however git is asked: the deletion belongs to the tree the file left and the
    addition to the tree it arrived in. Staging neither left a committed file gone from a
    tracked path with nothing anywhere to say where it went — a bare `D` and an untracked
    copy, which is #208 dealt out by the code that cleans up after #208.
    """

    def a_committed_card_in_a_worktree(self):
        """A task file git tracks, sitting in a tree that is not the ledger's."""
        from claude_bestpractice.gitctx import resolve

        tree = self.add_worktree("worker")
        elsewhere = resolve(tree)
        card = tree / store.TIER_A_DIRNAME / plan.PLAN_DIR / plan.NEXT / "0009-carry-me.md"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(
            "---\nid: 0009\ntitle: carry me\nstate: next\npaths: src/app.py\n"
            "done_when: stated\n---\n\nbody\n",
            encoding="utf-8",
        )
        git(["add", "-f", "--", str(card)], tree)
        git(["commit", "-qm", "ledger"], tree)
        return elsewhere, tree, card

    def test_the_tree_it_left_has_the_deletion_staged(self):
        elsewhere, tree, card = self.a_committed_card_in_a_worktree()
        migrate.repair(elsewhere)

        self.assertFalse(card.exists(), "the card was never carried home")
        staged = git(["diff", "--cached", "--name-status"], tree)
        self.assertIn("D\t", staged)
        self.assertIn(card.name, staged)

    def test_the_tree_it_arrived_in_gets_the_card_and_no_addition(self):
        """This repair on its own. It used to stage the addition as well, which kept a
        carried-home card from reading as a loss — and put the ledger back into the main
        checkout's index, which decision 0018 took it out of. The file is the part that was
        ever at risk, and it is there."""
        elsewhere, _tree, card = self.a_committed_card_in_a_worktree()
        migrate._carry_this_worktrees_tasks_home(elsewhere)

        self.assertTrue((plan.plan_dir(self.ctx(), plan.NEXT) / card.name).is_file())
        self.assertEqual("", git(["diff", "--cached", "--name-only"], self.repo))

    def test_after_the_whole_upgrade_the_card_is_home_and_untracked(self):
        elsewhere, _tree, card = self.a_committed_card_in_a_worktree()
        migrate.repair(elsewhere)

        self.assertTrue((plan.plan_dir(self.ctx(), plan.NEXT) / card.name).is_file())
        self.assertEqual("", git(["ls-files", store.TIER_A_DIRNAME + "/plan"], self.repo))

    def test_a_card_the_founder_never_committed_stages_nothing(self):
        """Decision 0008: where the ledger is not in their history, this adds it to no
        index. A repair that commits files for them is the plugin granting itself a place
        in their history."""
        from claude_bestpractice.gitctx import resolve

        tree = self.add_worktree("worker")
        card = tree / store.TIER_A_DIRNAME / plan.PLAN_DIR / plan.NEXT / "0009-untracked.md"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(
            "---\nid: 0009\ntitle: untracked\nstate: next\npaths: src/app.py\n"
            "done_when: stated\n---\n\nbody\n",
            encoding="utf-8",
        )

        migrate.repair(resolve(tree))
        self.assertEqual("", git(["diff", "--cached", "--name-only"], tree).strip())
        self.assertEqual("", git(["diff", "--cached", "--name-only"], self.repo).strip())


class TestANumberCarriedToTheNextPullRequestIsForgotten(RepoCase):
    """`opened` filled a new obligation's number from the branch's previous record, so the
    second pull request on a branch whose first had merged was filed as that first number.
    The fix stops new ones; the ones already filed stay up to thirty days unless repaired."""

    def filed(self, **row) -> None:
        from claude_bestpractice import pullrequest

        store.append_jsonl(store.tier_b(self.ctx(), pullrequest.PR_FILE), {
            "branch": "feat/x", "base": "main", "url": "", "session_id": "s1",
            "opened_at": 1.0, "handed_off_at": 0.0, **row,
        })

    def test_the_inherited_number_is_taken_back(self):
        from claude_bestpractice import pullrequest

        self.filed(number=41, state="open")
        self.filed(number=41, state="merged")
        self.filed(number=41, state="open")

        changed = migrate.repair(self.ctx())

        self.assertEqual(0, pullrequest._records(self.ctx())["feat/x"]["number"])
        self.assertTrue([line for line in changed if "previous one" in line], changed)

    def test_a_number_the_next_one_learned_for_itself_is_kept(self):
        from claude_bestpractice import pullrequest

        self.filed(number=41, state="merged")
        self.filed(number=42, state="open")
        migrate.repair(self.ctx())
        self.assertEqual(42, pullrequest._records(self.ctx())["feat/x"]["number"])


class TestARedSuiteThatNeverRanIsForgotten(RepoCase):
    """`No module named pytest` was filed as a red suite until 1.69.0 — on every board as
    "fix it before new work", and against every merge — for a run that reached no code.
    The gate stopped writing them; this takes back the one already on disk, which nothing
    the gate runs could ever clear.
    """

    def recorded(self, tail: str) -> None:
        from claude_bestpractice import evidence

        evidence.record_red(self.ctx(), ["python3", "-m", "pytest", "-q"], tail)

    def test_a_record_of_a_runner_that_was_not_installed_is_dropped(self):
        from claude_bestpractice import evidence

        self.recorded("/usr/local/bin/python3: No module named pytest")
        changed = migrate.repair(self.ctx())

        self.assertIsNone(evidence.red(self.ctx()))
        self.assertTrue([line for line in changed if "never reached the code" in line],
                        "a repair that changes what the board says must say so")

    def test_a_suite_that_really_failed_stays_red(self):
        from claude_bestpractice import evidence

        self.recorded("E   ModuleNotFoundError: No module named 'calc'\n1 error in 0.12s")
        migrate.repair(self.ctx())
        self.assertIsNotNone(evidence.red(self.ctx()))


class TestCredentialsAFailingSuitePrintedAreScrubbed(RepoCase):
    """Until 1.69.0 the end of a failing run's output was kept unscrubbed: in the red-suite
    record, and through an unverified finish's reason in its attempt, the unverified marker
    and the open item. The gate scrubs before it writes now; this is what it wrote before."""

    SAID = "ConnectionError: could not reach postgres://billing:Pr0dS3cretPass99@db.prod.example.com/b"

    def test_every_record_of_it_is_scrubbed_and_nothing_else_moves(self):
        from claude_bestpractice import attempts, board, evidence

        ctx = self.ctx()
        store.write_json(store.tier_a(ctx, evidence.RED_SUITE_FILE),
                         {"command": ["make", "test"], "tail": self.SAID, "executed": 1})
        attempts.record(ctx, title="unverified finish", why=f"Finished without proof. {self.SAID}",
                        paths=["db.py"])
        attempts.record(ctx, title="a session's own note", why="kept as the session wrote it",
                        paths=["db.py"])
        store.append_jsonl(store.tier_b(ctx, "unverified.jsonl"),
                           {"branch": "main", "reason": self.SAID})
        store.append_jsonl(store.tier_b(ctx, board.OPEN_ITEMS_FILE),
                           {"id": "x", "text": f"UNVERIFIED finish on main: {self.SAID}"})

        changed = migrate.repair(ctx)

        written = [store.tier_a(ctx, evidence.RED_SUITE_FILE),
                   store.tier_b(ctx, "unverified.jsonl"), store.tier_b(ctx, board.OPEN_ITEMS_FILE),
                   *attempts.attempts_dir(ctx).glob("*.md")]
        self.assertEqual([], [p.name for p in written if "Pr0dS3cretPass99" in p.read_text()])
        self.assertEqual(["make", "test"], evidence.red(ctx)["command"])
        self.assertIn("kept as the session wrote it",
                      "".join(p.read_text() for p in attempts.attempts_dir(ctx).glob("*.md")))
        self.assertTrue([line for line in changed if "scrubbed" in line])


class TestAGreenStampedOverAChangedTrackedFileIsRunAgain(RepoCase):
    """Until 1.69.0 a green was stamped with HEAD's tree while a tracked file whose name looked
    like a run's leftovers was changed, and the pre-push hook skips a tree on record as green.
    Which stamps were written that way cannot be told, so every older stamp goes."""

    def test_the_stamp_goes_and_the_green_stays(self):
        from claude_bestpractice import evidence

        self.write("src/app.py", "x = 1\n")
        self.commit("the app")
        evidence.record_green(self.ctx(), ["pytest"])
        self.assertTrue(evidence.green_covers_tree(self.ctx()), "precondition: stamped")

        changed = migrate.repair(self.ctx())

        self.assertFalse(evidence.green_covers_tree(self.ctx()))
        self.assertEqual(["pytest"], evidence.last_green(self.ctx())["command"])
        self.assertTrue([line for line in changed if "green record" in line])


class TestTheSharedVerificationTokenIsTakenAway(RepoCase):
    """Every worktree's verification run shared one token file, and the clean re-run left its
    token in it. Each run holds its own now; the old file is read by nothing."""

    def test_it_is_dropped(self):
        path = store.tier_b(self.ctx(), "verifying.nonce")
        store.atomic_write(path, "0" * 32)
        changed = migrate.repair(self.ctx())
        self.assertFalse(path.exists())
        self.assertTrue([line for line in changed if "verification token" in line])


class TestAStatusLineSplitAtASpaceIsQuoted(RepoCase):
    """Ours was written as a bare path, and a shell split it at the first space in an
    install path: the bar showed nothing, and installing it again said it was there."""

    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.bar = self.tmp / "Jane Doe" / "bin" / "claude-bp-statusline"
        self.bar.parent.mkdir(parents=True)
        self.bar.write_text("#!/bin/sh\necho bar\n", encoding="utf-8")
        self.bar.chmod(0o755)

    def start_with(self, command: str) -> str:
        """A session start over a status line of `command`; what is configured after it."""
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"statusLine": {"type": "command", "command": command}}))
        proc = self.run_hook(
            "session-start",
            {"session_id": "s1", "hook_event_name": "SessionStart", "source": "startup"},
            env={**os.environ, "HOME": str(self.home)},
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(settings.read_text(encoding="utf-8"))["statusLine"]["command"]

    def test_the_next_session_start_quotes_it(self):
        command = self.start_with(str(self.bar))
        self.assertEqual(shlex.quote(str(self.bar)), command)
        shown = subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=30)
        self.assertEqual("bar", shown.stdout.strip(), shown.stderr)
        self.assertEqual("", limits.requote(self.home), "a second run has nothing left to fix")

    def test_their_own_status_line_is_left_alone(self):
        theirs = str(self.tmp / "Jane Doe" / "my bar.sh")
        self.assertEqual(theirs, self.start_with(theirs))


class TestASecretTheOldRedactionMissedIsTakenOut(RepoCase):
    """A batch of ingested signals kept a production Redis password and an API key, and a
    captured turn could keep a private key's body: the redaction knew none of those shapes.
    The fix changes what is written next; this is what had already been written."""

    def signal_and_checkpoint(self):
        signal = self.write(".claude/signals/redis1.md", (
            "```text\nmessage: Error 111 connecting to "
            "redis://:Prod-R3dis-Passw0rd-2026@redis-master:6379/0\n"
            "X-Api-Key: 9f8e7d6c5b4a39281706f5e4d3c2b1a0\n```\n"))
        checkpoint = store.tier_a(self.ctx(), "checkpoints", "20260901-000000-s1.md")
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(
            "## Recent turns\n\n- -----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n"
            "-----END RSA PRIVATE KEY-----\n", encoding="utf-8")
        return signal, checkpoint

    def test_the_next_session_start_takes_it_out_and_says_so(self):
        signal, checkpoint = self.signal_and_checkpoint()
        proc = self.run_hook("session-start", {
            "session_id": "s1", "hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(0, proc.returncode, proc.stderr)
        for secret in ("Prod-R3dis-Passw0rd-2026", "9f8e7d6c5b4a3928"):
            self.assertNotIn(secret, signal.read_text(encoding="utf-8"))
        self.assertNotIn("MIIEowIBAAKCAQEAu1SU1Lf", checkpoint.read_text(encoding="utf-8"))
        self.assertIn("redis-master", signal.read_text(encoding="utf-8"), "only the secret goes")
        self.assertIn("signal or checkpoint", proc.stdout, "a repair that rewrites files says so")

    def test_a_clean_file_is_not_touched(self):
        clean = self.write(".claude/signals/clean.md", "```text\nmessage: KeyError 'rate'\n```\n")
        before = clean.stat().st_mtime_ns
        self.assertFalse([line for line in migrate.repair(self.ctx()) if "0018" in line])
        self.assertEqual(before, clean.stat().st_mtime_ns)


class TestARedSuiteCountedUnderTheWrongConfigurationCanClear(RepoCase):
    """Until 1.69.2 the gate started pytest once, at the suite's root, and a project below it
    with its own configuration ran under none (#230); a workspace member lost the workspace
    root's the same way. The record of that run holds its count as the mark a green must reach,
    and the run as configured is a different number of tests, so nothing could clear it; its
    tree hash would also have it re-asserted instead of run."""

    def a_project(self, where: str = "backend/") -> None:
        self.write(f"{where}pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src"]\n')
        self.write(f"{where}src/sample/__init__.py", "def f():\n    return 1\n")
        self.write(f"{where}tests/test_sample.py",
                   "from sample import f\n\n\ndef test_f():\n    assert f() == 1\n")

    def recorded_by_the_root_run(self, command: tuple = ("pytest",)) -> dict:
        from claude_bestpractice import evidence

        ctx = self.ctx()
        evidence.record_red(ctx, list(command), "226 failed, 4368 passed in 60s", None,
                            evidence.tree_hash(ctx), executed=4594)
        return evidence.red(ctx)

    def verify(self):
        from claude_bestpractice import evidence

        return evidence._verify_by_running(self.ctx(), [], ["make", "test"], ["backend/app.py"])

    def test_the_run_as_configured_clears_it_after_the_upgrade(self):
        from claude_bestpractice import evidence

        self.a_project()
        self.commit("a project with a configuration of its own")
        self.recorded_by_the_root_run()
        self.assertFalse(self.verify().ok, "precondition: the old failure stands on this tree")

        changed = migrate.repair(self.ctx())

        record = evidence.red(self.ctx())
        self.assertEqual(["pytest"], record["command"], "the record is still red")
        self.assertNotIn("executed", record)
        self.assertNotIn("tree_hash", record)
        self.assertTrue([line for line in changed if "suite's configuration" in line])
        verdict = self.verify()
        self.assertTrue(verdict.ok, verdict.reason)
        self.assertIsNone(evidence.red(self.ctx()), "a passing run as configured left it red")

    def test_a_suite_with_one_configuration_keeps_its_mark(self):
        from claude_bestpractice import evidence

        self.write("tests/test_sample.py", "def test_f():\n    assert True\n")
        self.commit("one configuration")
        before = self.recorded_by_the_root_run()
        migrate.repair(self.ctx())
        self.assertEqual(before, evidence.red(self.ctx()))

    def test_a_record_of_the_projects_own_command_is_left_alone(self):
        from claude_bestpractice import evidence

        self.a_project()
        self.commit("a project with a configuration of its own")
        before = self.recorded_by_the_root_run(("make", "test"))
        migrate.repair(self.ctx())
        self.assertEqual(before, evidence.red(self.ctx()))

    def test_a_member_run_without_the_workspace_configuration_is_uncounted_too(self):
        from claude_bestpractice import evidence, suites

        self.write("pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["packages/api/src"]\n')
        self.write("packages/api/pyproject.toml", '[project]\nname = "api"\nversion = "0"\n')
        self.write("packages/api/tests/test_api.py", "def test_api():\n    assert True\n")
        self.commit("a workspace configured once, at its root")
        member = suites.Suite("packages/api/", ("python3", "-m", "pytest", "-q"), False)
        evidence.record_red(self.ctx(), ["pytest"], "1 failed", member,
                            evidence.tree_hash(self.ctx()), executed=40)

        migrate.repair(self.ctx())

        record = evidence.red(self.ctx())
        self.assertEqual("packages/api/", record["path"])
        self.assertNotIn("executed", record)
        self.assertNotIn("tree_hash", record)
