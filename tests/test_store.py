"""Substrate: atomic writes, locks that actually lock, tier separation."""

from __future__ import annotations

import json
import multiprocessing
import os
import time
import unittest
from pathlib import Path

from helpers import RepoCase

from founder_os import store


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

        from founder_os.gitctx import resolve

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
        self.assertFalse(store.tier_b(ctx).exists())


if __name__ == "__main__":
    unittest.main()
