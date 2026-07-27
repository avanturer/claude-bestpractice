"""Session registry and leases — the cross-session visibility layer."""

from __future__ import annotations

import time
import unittest

from helpers import RepoCase, session_record_for

from claude_bestpractice import sessions, store


record = session_record_for


class TestRegistry(RepoCase):
    def test_register_and_get(self):
        ctx = self.ctx()
        sessions.register(ctx, record(ctx, "s1"))
        got = sessions.get(ctx, "s1")
        self.assertIsNotNone(got)
        self.assertEqual(got.session_id, "s1")

    def test_sessions_do_not_share_a_file(self):
        """One file per session is the whole concurrency story."""
        ctx = self.ctx()
        for i in range(5):
            sessions.register(ctx, record(ctx, f"s{i}"))
        files = list((store.tier_b(ctx, "sessions")).glob("*.json"))
        self.assertEqual(len(files), 5)

    def test_session_id_is_sanitised_into_a_filename(self):
        ctx = self.ctx()
        sessions.register(ctx, record(ctx, "../../etc/passwd"))
        files = list((store.tier_b(ctx, "sessions")).glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertNotIn("..", files[0].name)

    def test_touch_refreshes_heartbeat_and_fields(self):
        ctx = self.ctx()
        rec = record(ctx, "s1")
        rec.heartbeat_at = time.time() - 100
        sessions.register(ctx, rec)  # register stamps a fresh heartbeat
        sessions.touch(ctx, "s1", task_statement="do the thing")
        got = sessions.get(ctx, "s1")
        self.assertEqual(got.task_statement, "do the thing")
        self.assertLess(time.time() - got.heartbeat_at, 5)

    def test_touch_on_unknown_session_is_a_noop(self):
        self.assertIsNone(sessions.touch(self.ctx(), "ghost", task_statement="x"))


class TestLiveness(RepoCase):
    def test_dead_pid_is_not_live(self):
        ctx = self.ctx()
        self.assertFalse(sessions.pid_alive(999_999_999))
        rec = record(ctx, "dead", pid=999_999_999)
        self.assertFalse(sessions.is_live(ctx, rec))

    def test_a_quiet_session_is_still_live(self):
        """Silence is what a working session looks like while a human reads.

        Reaping on a quiet heartbeat was the worst defect this plugin has had: the
        record was deleted out from under a running session, and every gate then took
        its missing-record branch and enforced nothing for the rest of that session.
        """
        ctx = self.ctx()
        rec = record(ctx, "thinking")
        rec.heartbeat_at = time.time() - (sessions.HEARTBEAT_STALE_SECONDS + 60)
        self.assertTrue(sessions.is_live(ctx, rec))
        self.assertTrue(sessions.is_idle(rec), "the board should still dim it")

    def test_a_record_older_than_the_hard_ceiling_is_dead(self):
        """The backstop against a record that outlived a reboot."""
        ctx = self.ctx()
        rec = record(ctx, "ancient")
        rec.heartbeat_at = time.time() - (sessions.HEARTBEAT_DEAD_SECONDS + 60)
        self.assertFalse(sessions.is_live(ctx, rec))

    def test_a_recycled_pid_is_not_the_same_session(self):
        """A live pid proves nothing if it belongs to a different process now."""
        ctx = self.ctx()
        rec = record(ctx, "recycled")
        rec.pid_fingerprint = "999999999"
        if not sessions.pid_fingerprint(rec.pid):
            self.skipTest("kernel does not expose process start times")
        self.assertFalse(sessions.is_live(ctx, rec))

    def test_an_unknown_fingerprint_never_reaps(self):
        """Cannot-tell must resolve to live; the cost of a wrong reap is a dead gate."""
        ctx = self.ctx()
        rec = record(ctx, "unknown")
        rec.pid_fingerprint = ""
        self.assertTrue(sessions.is_live(ctx, rec))

    def test_unregistered_worktree_is_not_live(self):
        """A live pid is not enough: the worktree must still exist."""
        ctx = self.ctx()
        rec = record(ctx, "moved")
        rec.worktree = "/nonexistent/worktree"
        self.assertFalse(sessions.is_live(ctx, rec))

    def test_failed_worktree_probe_does_not_reap(self):
        """An empty known-set means the probe failed; do not reap on bad data."""
        ctx = self.ctx()
        rec = record(ctx, "safe")
        self.assertTrue(sessions.is_live(ctx, rec, known_worktrees=set()))


class TestReaper(RepoCase):
    def test_reap_removes_dead_and_keeps_live(self):
        ctx = self.ctx()
        sessions.register(ctx, record(ctx, "live"))
        sessions.register(ctx, record(ctx, "dead", pid=999_999_999))

        reaped = sessions.reap(ctx)
        self.assertEqual([r.session_id for r in reaped], ["dead"])
        self.assertIsNone(sessions.get(ctx, "dead"))
        self.assertIsNotNone(sessions.get(ctx, "live"))

    def test_reap_releases_the_dead_session_leases(self):
        """A crashed session must not poison a path forever."""
        ctx = self.ctx()
        sessions.register(ctx, record(ctx, "dead", pid=999_999_999))
        sessions.acquire_lease(ctx, "dead", "src/app.py")
        self.assertEqual(sessions.leases_held_by(ctx, "dead"), ["src/app.py"])

        sessions.reap(ctx)
        self.assertEqual(sessions.leases_held_by(ctx, "dead"), [])
        self.assertIsNone(sessions.acquire_lease(ctx, "other", "src/app.py"))

    def test_live_sessions_excludes_self(self):
        ctx = self.ctx()
        sessions.register(ctx, record(ctx, "me"))
        sessions.register(ctx, record(ctx, "you"))
        others = sessions.live_sessions(ctx, exclude="me")
        self.assertEqual([r.session_id for r in others], ["you"])


class TestLeases(RepoCase):
    def test_acquire_then_conflict(self):
        ctx = self.ctx()
        self.assertIsNone(sessions.acquire_lease(ctx, "a", "src/x.py"))
        self.assertEqual(sessions.acquire_lease(ctx, "b", "src/x.py"), "a")

    def test_reacquiring_own_lease_is_idempotent(self):
        ctx = self.ctx()
        sessions.acquire_lease(ctx, "a", "src/x.py")
        self.assertIsNone(sessions.acquire_lease(ctx, "a", "src/x.py"))

    def test_expired_lease_is_taken_over(self):
        ctx = self.ctx()
        sessions.acquire_lease(ctx, "a", "src/x.py", ttl=-1)
        self.assertIsNone(sessions.acquire_lease(ctx, "b", "src/x.py"))
        self.assertEqual(sessions.leases_held_by(ctx, "b"), ["src/x.py"])

    def test_lease_from_dead_process_is_taken_over(self):
        ctx = self.ctx()
        with store.guarded_json(store.tier_b(ctx, sessions.LEASES_FILE), default={}) as box:
            box[0] = {
                "src/x.py": {
                    "session_id": "ghost",
                    "pid": 999_999_999,
                    "acquired_at": time.time(),
                    "expires_at": time.time() + 9999,
                }
            }
        self.assertIsNone(sessions.acquire_lease(ctx, "b", "src/x.py"))

    def test_release_all_clears_only_that_session(self):
        ctx = self.ctx()
        sessions.acquire_lease(ctx, "a", "one.py")
        sessions.acquire_lease(ctx, "b", "two.py")
        sessions.release_all(ctx, "a")
        self.assertEqual(sessions.leases_held_by(ctx, "a"), [])
        self.assertEqual(sessions.leases_held_by(ctx, "b"), ["two.py"])

    def corrupt_table(self) -> None:
        """One unreadable row beside a good one, the shape a torn write leaves behind."""
        with store.guarded_json(store.tier_b(self.ctx(), sessions.LEASES_FILE), default={}) as box:
            box[0] = {
                "src/broken.py": "not a lease at all",
                "src/fine.py": {
                    "session_id": "a",
                    "pid": 999_999_999,
                    "acquired_at": time.time(),
                    "expires_at": time.time() + 9999,
                },
            }

    def test_a_corrupt_row_does_not_refuse_every_write_on_the_clone(self):
        """Every lease call must survive one malformed row, because the caller is a gate.

        The table was type-checked and its rows were not, so `holder.get(...)` raised
        AttributeError inside the fail-closed pre-write gate. A single bad byte in an
        ephemeral cache file would refuse every write in every session until a human
        found and deleted it — while the doctor still reported all checks passing.
        """
        ctx = self.ctx()
        self.corrupt_table()
        self.assertIsNone(sessions.acquire_lease(ctx, "b", "src/broken.py"))
        self.assertEqual(sessions.leases_held_by(ctx, "b"), ["src/broken.py"])

    def test_a_corrupt_row_does_not_take_the_good_rows_with_it(self):
        ctx = self.ctx()
        self.corrupt_table()
        sessions.acquire_lease(ctx, "b", "src/other.py")
        self.assertEqual(sessions.leases_held_by(ctx, "a"), ["src/fine.py"])

    def test_release_survives_a_corrupt_row(self):
        ctx = self.ctx()
        self.corrupt_table()
        sessions.release_all(ctx, "a")
        self.assertEqual(sessions.leases_held_by(ctx, "a"), [])


class TestCrossWorktree(RepoCase):
    def test_sibling_worktrees_see_each_other(self):
        """The gap no surveyed tool closes: siblings are mutually visible."""
        from claude_bestpractice.gitctx import resolve

        main_ctx = self.ctx()
        wt = self.add_worktree("feature")
        wt_ctx = resolve(wt)

        sessions.register(main_ctx, record(main_ctx, "on-main"))
        sessions.register(wt_ctx, record(wt_ctx, "on-feature"))

        from_main = {r.session_id for r in sessions.live_sessions(main_ctx)}
        from_wt = {r.session_id for r in sessions.live_sessions(wt_ctx)}
        self.assertEqual(from_main, {"on-main", "on-feature"})
        self.assertEqual(from_wt, from_main)

    def test_leases_are_shared_across_worktrees(self):
        from claude_bestpractice.gitctx import resolve

        main_ctx = self.ctx()
        wt_ctx = resolve(self.add_worktree("feature"))

        self.assertIsNone(sessions.acquire_lease(main_ctx, "main-sess", "src/shared.py"))
        self.assertEqual(sessions.acquire_lease(wt_ctx, "wt-sess", "src/shared.py"), "main-sess")


if __name__ == "__main__":
    unittest.main()
