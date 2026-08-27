"""Substrate: atomic writes, locks that actually lock, tier separation."""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import RepoCase

from claude_bestpractice import store


def _contend(args) -> int:
    """Child process: take the lock, increment, release. Run in a process pool."""
    path_str, lock_str = args
    path, lock = Path(path_str), Path(lock_str)
    with store.file_lock(lock, timeout=30):
        current = store.read_json(path, default={"n": 0})
        time.sleep(0.005)  # widen the race window
        current["n"] += 1
        store.write_json(path, current)
    return os.getpid()


class TestTiers(RepoCase):
    def test_tier_b_is_shared_across_worktrees(self):
        """The property the entire coordination design rests on."""
        main_ctx = self.ctx()
        wt = self.add_worktree("feature")

        from claude_bestpractice.gitctx import resolve

        wt_ctx = resolve(wt)

        self.assertNotEqual(main_ctx.worktree_root, wt_ctx.worktree_root)
        self.assertEqual(store.tier_b(main_ctx), store.tier_b(wt_ctx))
        self.assertNotEqual(store.tier_a(main_ctx), store.tier_a(wt_ctx))
        self.assertTrue(wt_ctx.is_worktree)
        self.assertFalse(main_ctx.is_worktree)

    def test_tier_b_lives_outside_the_working_tree(self):
        """Tier B must never show up in `git status` or a merge."""
        ctx = self.ctx()
        store.write_json(store.tier_b(ctx, "x.json"), {"a": 1})
        from helpers import git

        self.assertEqual(git(["status", "--porcelain"], self.repo), "")


class TestTheRepositoryResolvesTheSameFromAnySubdirectory(RepoCase):
    """`git rev-parse --git-common-dir` answers relative to the directory it ran in;
    `--show-toplevel` answers absolutely. Joining the first to the second agreed only
    while the two were the same directory — that is, only from the repository root.

    One `cd` into a subdirectory and the common dir walked out of the repository by
    exactly the depth of that subdirectory. In a repository at `/home/<user>/dev/fuddy`,
    `cd backend/src/fuddy/merge` resolved it to `/home/.git`, four levels up. The
    harness shell keeps the `cd`, so every later call resolved it there too (#187).

    Both harms come from that one join, and the quiet one is worse:

    - `/home` is not writable, so the gate raised PermissionError and — being
      fail-closed — refused every tool call for the rest of the session, with no way
      back, because the `cd` that would fix it is itself a refused Bash call.
    - Where the wrong path IS writable nothing fails at all. Tier B moves to a directory
      no sibling reads, so the board, the leases and the observed test runs go to a
      second store that looks exactly like an empty repository.
    """

    DEEP = "backend/src/fuddy/merge"

    def deep_dir(self, root: Path) -> Path:
        target = root / self.DEEP
        target.mkdir(parents=True, exist_ok=True)
        return target

    def resolved(self, where: Path):
        from claude_bestpractice.gitctx import resolve

        return resolve(where)

    def test_the_common_dir_is_the_same_from_the_root_and_from_a_subdirectory(self):
        at_root = self.resolved(self.repo)
        deep = self.resolved(self.deep_dir(self.repo))
        self.assertEqual(at_root.common_dir, deep.common_dir)

    def test_the_common_dir_never_leaves_the_repository(self):
        """The shape of the report: four levels deep put it four levels above the repo."""
        deep = self.resolved(self.deep_dir(self.repo))
        self.assertTrue(
            str(deep.common_dir).startswith(str(self.repo.resolve())),
            f"the common dir resolved outside the repository: {deep.common_dir}",
        )

    def test_state_is_the_same_store_from_a_subdirectory(self):
        """Tier B is what every sibling session reads. A second one is invisible, not loud."""
        at_root = self.resolved(self.repo)
        deep = self.resolved(self.deep_dir(self.repo))
        self.assertEqual(store.tier_b(at_root), store.tier_b(deep))

    def test_a_main_checkout_does_not_report_itself_as_a_worktree(self):
        """`is_worktree` compares the git dir against the common dir, so a common dir
        resolved somewhere else flips it — and gates key on it."""
        self.assertFalse(self.resolved(self.deep_dir(self.repo)).is_worktree)

    def test_a_worktree_still_reports_itself_as_one_from_a_subdirectory(self):
        """The other direction. Anchoring both answers to the same directory must not
        cost the distinction the coordination layer is built on."""
        wt = self.add_worktree("feature")
        deep = self.resolved(self.deep_dir(wt))
        self.assertTrue(deep.is_worktree)
        self.assertEqual(self.resolved(self.repo).common_dir, deep.common_dir)

    def test_neither_gate_crashes_when_the_wrong_path_cannot_be_used(self):
        """The founder's session was unable to act AND unable to stop.

        PreToolUse refused Bash, Write and EnterWorktree; the Stop gate refused the turn
        with the same PermissionError, so it could not even finish. Only a restart got
        out. Both gates resolve the repository through the same call, so both inherited
        the same wrong path.

        Reproduced rather than approximated: `/home` cannot be written to, so a plain
        FILE is planted exactly where the wrong join lands. One directory down is enough —
        the join walks up by the depth of the subdirectory, so depth one keeps the planted
        file inside the fixture instead of somewhere real.
        """
        (self.repo / "sub").mkdir()
        (self.tmp / ".git").write_text("not a directory\n", encoding="utf-8")

        gates = (
            ("pre-tool", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                          "tool_input": {"command": "pwd"}}),
            ("evidence-gate", {"hook_event_name": "Stop", "stop_hook_active": False}),
        )
        for gate, event in gates:
            proc = self.run_hook(gate, {"session_id": "s1", **event}, cwd=self.repo / "sub")
            self.assertNotIn("gate failed", proc.stdout + proc.stderr,
                             f"{gate} crashed and failed closed from a subdirectory")

    def test_the_gate_writes_its_state_where_the_siblings_read_it(self):
        """End to end, through the hook the founder actually met.

        Asserting "it did not crash" would prove nothing here: the crash needs the wrong
        path to be UNWRITABLE, which is true of `/home` on their machine and false of a
        temporary directory. Under a fixture the same bug is silent — it just writes the
        session registry somewhere no sibling looks. That is the invariant, so that is
        what is asserted, and it fails wherever the join is wrong.
        """
        from claude_bestpractice import sessions
        from helpers import sid

        deep = self.deep_dir(self.repo)
        self.run_hook("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
        }, cwd=deep)

        seen = sessions.get(self.resolved(self.repo), sid(self.repo, "s1"))
        self.assertIsNotNone(
            seen, "a call made from a subdirectory registered in a store nobody else reads")


class TestAtomicWrite(RepoCase):
    def test_write_and_read_roundtrip(self):
        ctx = self.ctx()
        path = store.tier_b(ctx, "r.json")
        store.write_json(path, {"k": "v"})
        self.assertEqual(store.read_json(path), {"k": "v"})

    def test_mode_is_private(self):
        ctx = self.ctx()
        path = store.tier_b(ctx, "p.json")
        store.write_json(path, {})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_no_temp_files_survive(self):
        ctx = self.ctx()
        path = store.tier_b(ctx, "t.json")
        store.write_json(path, {"k": 1})
        leftovers = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_corrupt_file_reads_as_default_rather_than_raising(self):
        """One torn record must not make the whole store unreadable."""
        ctx = self.ctx()
        path = store.tier_b(ctx, "c.json")
        store.ensure_dir(path.parent)
        path.write_text("{ this is not json")
        self.assertEqual(store.read_json(path, default={"fallback": True}), {"fallback": True})

    def test_failed_write_leaves_no_partial_file(self):
        ctx = self.ctx()
        path = store.tier_b(ctx, "fail.json")

        class Boom(Exception):
            pass

        class Exploding(str):
            def encode(self, *a, **kw):
                raise Boom("no")

        with self.assertRaises(Boom):
            store.atomic_write(path, Exploding("x"))
        self.assertFalse(path.exists())
        self.assertEqual([p for p in path.parent.iterdir() if p.name.endswith(".tmp")], [])


class TestJsonl(RepoCase):
    def test_append_and_read(self):
        ctx = self.ctx()
        path = store.tier_b(ctx, "log.jsonl")
        for i in range(5):
            store.append_jsonl(path, {"i": i})
        self.assertEqual([r["i"] for r in store.read_jsonl(path)], [0, 1, 2, 3, 4])

    def test_torn_line_is_skipped_not_fatal(self):
        ctx = self.ctx()
        path = store.tier_b(ctx, "torn.jsonl")
        store.append_jsonl(path, {"i": 1})
        with path.open("a") as fh:
            fh.write('{"i": 2, truncat\n')
        store.append_jsonl(path, {"i": 3})
        self.assertEqual([r["i"] for r in store.read_jsonl(path)], [1, 3])


class TestLocks(RepoCase):
    def test_lock_is_exclusive(self):
        ctx = self.ctx()
        lock = store.tier_b(ctx, "a.lock")
        with store.file_lock(lock):
            with self.assertRaises(store.LockTimeout):
                with store.file_lock(lock, timeout=0.1):
                    pass

    def test_lock_released_on_exception(self):
        ctx = self.ctx()
        lock = store.tier_b(ctx, "b.lock")
        with self.assertRaises(ValueError):
            with store.file_lock(lock):
                raise ValueError("boom")
        with store.file_lock(lock, timeout=1):
            pass

    def test_stale_lock_is_reclaimed(self):
        """A crashed holder must not wedge the repository forever."""
        ctx = self.ctx()
        lock = store.tier_b(ctx, "stale.lock")
        store.ensure_dir(lock.parent)
        lock.write_text(json.dumps({"pid": 999999999, "acquired_at": 0}))
        old = time.time() - 600
        os.utime(lock, (old, old))

        with store.file_lock(lock, timeout=2, stale_after=60):
            pass  # acquired by reclaiming
        self.assertFalse(lock.exists())

    def test_fresh_lock_is_not_stolen(self):
        ctx = self.ctx()
        lock = store.tier_b(ctx, "fresh.lock")
        with store.file_lock(lock):
            with self.assertRaises(store.LockTimeout):
                with store.file_lock(lock, timeout=0.2, stale_after=3600):
                    pass

    def dead_pid(self) -> int:
        """A pid that certainly no longer names a process: spawn one and reap it."""
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait()
        return proc.pid

    def plant(self, name: str, payload: dict) -> Path:
        lock = store.tier_b(self.ctx(), name)
        store.ensure_dir(lock.parent)
        lock.write_text(json.dumps(payload))
        return lock

    def test_a_crashed_holder_does_not_wedge_the_clone_for_two_minutes(self):
        """The whole point of the pid rule: no waiting out `stale_after` after a crash.

        Time alone meant one session dying at the wrong moment failed every other
        session's writes for two minutes — and these gates fail closed, so "failed" is
        "refused everything".
        """
        lock = self.plant(
            "crashed.lock",
            {"pid": self.dead_pid(), "acquired_at": time.time(), "identity": store.lock_identity()},
        )
        started = time.monotonic()
        with store.file_lock(lock, timeout=2, stale_after=86400):
            pass
        self.assertLess(time.monotonic() - started, 1.0, "waited on a lock whose holder is gone")

    def test_a_pid_from_another_namespace_is_never_reasoned_about(self):
        """Container B's pid 1234 is not container A's. Same host, same clock, same file."""
        lock = self.plant(
            "foreign.lock",
            {"pid": self.dead_pid(), "acquired_at": time.time(), "identity": "elsewhere/pid:[1]"},
        )
        with self.assertRaises(store.LockTimeout):
            with store.file_lock(lock, timeout=0.3, stale_after=3600):
                pass

    def test_a_payload_that_never_landed_falls_back_to_time(self):
        """O_EXCL creates the file before the write. A reader can land in that window."""
        lock = self.plant("empty.lock", {})
        lock.write_bytes(b"")
        with self.assertRaises(store.LockTimeout):
            with store.file_lock(lock, timeout=0.3, stale_after=3600):
                pass

        old = time.time() - 600
        os.utime(lock, (old, old))
        with store.file_lock(lock, timeout=2, stale_after=60):
            pass

    def test_concurrent_processes_do_not_lose_updates(self):
        """The lost-update race, run for real across processes."""
        ctx = self.ctx()
        path = store.tier_b(ctx, "counter.json")
        lock = store.tier_b(ctx, "counter.lock")
        store.write_json(path, {"n": 0})

        workers = 8
        with multiprocessing.get_context("fork").Pool(workers) as pool:
            pool.map(_contend, [(str(path), str(lock))] * workers)

        self.assertEqual(store.read_json(path)["n"], workers)


class TestGuardedJson(RepoCase):
    def test_reads_inside_the_lock(self):
        ctx = self.ctx()
        path = store.tier_b(ctx, "g.json")
        store.write_json(path, {"n": 1})
        with store.guarded_json(path, default={}) as box:
            box[0]["n"] += 1
        self.assertEqual(store.read_json(path), {"n": 2})

    def test_purge_removes_everything_derived(self):
        ctx = self.ctx()
        store.write_json(store.tier_b(ctx, "deep", "x.json"), {"a": 1})
        self.assertTrue(store.tier_b(ctx).exists())
        store.purge_tier_b(ctx)
        self.assertFalse(store.tier_b(ctx, "deep").exists())

    def test_purge_keeps_the_records_that_cannot_be_rebuilt(self):
        """Tier B is described as entirely derived, and some of it records EVENTS.

        A finish that could not be proved, a decision drafted and not yet accepted: no
        amount of rescanning the repository brings an event back. `rmtree` took them, and
        `claude-bp reindex` printed "Nothing durable was lost" over the top of it.
        """
        ctx = self.ctx()
        store.write_json(store.tier_b(ctx, "derived.json"), {"cache": True})
        for name in store.CARRIED:
            store.append_jsonl(store.tier_b(ctx, name), {"kept": name})

        store.purge_tier_b(ctx)

        self.assertFalse(store.tier_b(ctx, "derived.json").exists())
        for name in store.CARRIED:
            self.assertEqual(
                store.read_jsonl(store.tier_b(ctx, name)), [{"kept": name}], name
            )


class TestWhatAnIgnoreRuleIsCosting(RepoCase):
    """The board reported that Tier A was hidden and never said what that costs. "A layer
    is hidden" is an abstraction; "two records die with this clone" is a decision."""

    def hide(self) -> None:
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(f"{store.TIER_A_DIRNAME}/attempts/\n")

    def test_records_an_ignore_rule_keeps_out_of_git_are_counted(self):
        self.hide()
        self.write(f"{store.TIER_A_DIRNAME}/attempts/0001-a.md", "what failed\n")
        self.write(f"{store.TIER_A_DIRNAME}/attempts/0002-b.md", "what else failed\n")
        self.assertEqual(2, len(store.ignored_tier_a(self.ctx())))

    def test_a_record_committed_before_the_rule_appeared_is_not_counted(self):
        """It is tracked, so it is not lost — and counting it would make the number
        untrustworthy at the moment somebody has to act on it."""
        self.write(f"{store.TIER_A_DIRNAME}/attempts/0001-a.md", "what failed\n")
        self.commit("keep the ledger")
        self.hide()
        self.assertEqual([], store.ignored_tier_a(self.ctx()))

    def test_nothing_hidden_counts_nothing(self):
        self.write(f"{store.TIER_A_DIRNAME}/attempts/0001-a.md", "what failed\n")
        self.assertEqual([], store.ignored_tier_a(self.ctx()))


class TestAWriteDoesNotEatTheFoundersSymlink(unittest.TestCase):
    """`~/.claude/settings.json` is written from a hook on every session.

    A dotfile manager (nix/home-manager, stow, chezmoi) keeps that path as a link into its
    own repository. `os.replace` onto the link leaves a regular file where the link was, so
    the link does not survive the first session and the next `switch` either conflicts or
    silently reverts everything the plugin wrote. Claude Code fixed the same shape in its
    sandbox cleanup in 2.1.247.
    """

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.target = self.home / "dotfiles" / "settings.json"
        self.target.parent.mkdir(parents=True)
        self.target.write_text('{"managed": true}\n')
        self.link = self.home / ".claude" / "settings.json"
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to(self.target)

    def test_the_link_survives_the_write(self):
        store.atomic_write(self.link, '{"added": 1}\n')
        self.assertTrue(self.link.is_symlink(), "the link was replaced by a regular file")

    def test_the_content_lands_where_the_link_points(self):
        store.atomic_write(self.link, '{"added": 1}\n')
        self.assertEqual('{"added": 1}', self.target.read_text().strip())
        self.assertEqual('{"added": 1}', self.link.read_text().strip())

    def test_a_dangling_link_is_repaired_rather_than_replaced(self):
        """A dotfiles checkout that has not been materialised yet is not a crash."""
        dangling = self.home / ".claude" / "other.json"
        dangling.symlink_to(self.home / "dotfiles" / "not-yet.json")
        store.atomic_write(dangling, '{"x": 1}\n')
        self.assertTrue(dangling.is_symlink())
        self.assertEqual('{"x": 1}', (self.home / "dotfiles" / "not-yet.json").read_text().strip())

    def test_an_ordinary_path_is_untouched_by_any_of_this(self):
        plain = self.home / "plain.json"
        store.atomic_write(plain, '{"y": 2}\n')
        self.assertFalse(plain.is_symlink())
        self.assertEqual('{"y": 2}', plain.read_text().strip())


if __name__ == "__main__":
    unittest.main()
