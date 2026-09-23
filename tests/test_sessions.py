"""Session registry and leases — the cross-session visibility layer."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import BIN, LIB, RepoCase, git, session_record_for, sid

from claude_bestpractice import sessions, store, worktree


record = session_record_for


class TestTheRepositoryHasOneName(RepoCase):
    """One repository read as two because the label came from the worktree directory.

    `fuddy` in the main checkout, `fuddy-envfix` in a worktree of it. The state was
    correctly shared the whole time — `repo_key` is the common dir — so only the label
    lied, but it lied in the one product whose stated scene is three to eight worktrees
    of a single repository.
    """

    def test_a_worktree_reports_the_repository_it_belongs_to(self):
        from claude_bestpractice.gitctx import resolve

        main = resolve(self.repo)
        side = resolve(self.add_worktree("feature-branch"))

        self.assertEqual(main.repo_name, side.repo_name)
        self.assertEqual(self.repo.name, main.repo_name)
        self.assertNotEqual(side.worktree_root.name, side.repo_name, "the fixture proves nothing")

    def test_it_agrees_with_the_identity_the_state_is_keyed_by(self):
        """A name derived from something other than `repo_key` would drift from it."""
        from claude_bestpractice.gitctx import resolve

        main = resolve(self.repo)
        side = resolve(self.add_worktree("other-branch"))
        self.assertEqual(main.repo_key, side.repo_key)


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
                sessions.lease_key(ctx, "src/x.py"): {
                    "session_id": "ghost",
                    "pid": 999_999_999,
                    # Without this the lease says nothing about whose process that pid
                    # was, and an unattributed pid is not grounds for taking a path off
                    # another session — see `_holder_stands`.
                    "pid_trust": sessions.PID_TRUST_OWNER,
                    "acquired_at": time.time(),
                    "expires_at": time.time() + 9999,
                }
            }
        self.assertIsNone(sessions.acquire_lease(ctx, "b", "src/x.py"))

    def test_a_lease_from_a_removed_worktree_does_not_stand(self):
        """Merge the branch, remove the tree, carry on — and be locked out by yourself.

        Entering a worktree fires no SessionStart, so `reap` — which does release the
        leases of a session whose tree git no longer lists — never runs between the two
        trees. The record survives with a live pid (it is the same CLI process), and the
        paths stay refused for the rest of the TTL by a message saying another session is
        editing files that no longer exist anywhere on disk (#100).
        """
        from claude_bestpractice.gitctx import resolve

        tree = self.add_worktree("topic")
        gone_ctx = resolve(tree)
        sessions.register(gone_ctx, record(gone_ctx, "old"))
        self.assertIsNone(sessions.acquire_lease(gone_ctx, "old", "src/shared.py"))

        git(["worktree", "remove", "--force", str(tree)], self.repo)

        ctx = self.ctx()
        self.assertIsNone(sessions.acquire_lease(ctx, "new", "src/shared.py"))
        self.assertEqual(sessions.leases_held_by(ctx, "new"), ["src/shared.py"])
        self.assertEqual([], sessions.elsewhere_on(ctx, "src/shared.py", "new"),
                         "a tree git no longer lists was reported as working on the file")

    def test_a_lease_from_a_live_worktree_is_reported(self):
        """The other half of the same rule, so the fix cannot be "nobody is ever named"."""
        from claude_bestpractice.gitctx import resolve

        wt_ctx = resolve(self.add_worktree("busy"))
        sessions.register(wt_ctx, record(wt_ctx, "sibling"))
        self.assertIsNone(sessions.acquire_lease(wt_ctx, "sibling", "src/shared.py"))
        self.assertEqual(["sibling"],
                         sessions.elsewhere_on(self.ctx(), "src/shared.py", "me"))

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
                sessions.lease_key(self.ctx(), "src/broken.py"): "not a lease at all",
                sessions.lease_key(self.ctx(), "src/fine.py"): {
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

    def test_a_lease_claims_a_file_and_two_trees_hold_two_files(self):
        """The refusal's own reasoning — "whoever writes second wins silently" — is true
        of two sessions in ONE tree, which `require_worktree` forbids. Across trees the
        second writer produces a merge, and git resolves merges (#163).

        The table is still shared: that is what lets each side be TOLD about the other.
        """
        from claude_bestpractice.gitctx import resolve

        main_ctx = self.ctx()
        wt_ctx = resolve(self.add_worktree("feature"))

        self.assertIsNone(sessions.acquire_lease(main_ctx, "main-sess", "src/shared.py"))
        self.assertIsNone(sessions.acquire_lease(wt_ctx, "wt-sess", "src/shared.py"))
        self.assertEqual(["main-sess"],
                         sessions.elsewhere_on(wt_ctx, "src/shared.py", "wt-sess"))
        self.assertEqual([], sessions.elsewhere_on(wt_ctx, "src/other.py", "wt-sess"))

    def test_two_sessions_in_one_tree_are_still_refused(self):
        """There the second write really does land on the first one's file."""
        self.assertIsNone(sessions.acquire_lease(self.ctx(), "a", "src/shared.py"))
        self.assertEqual("a", sessions.acquire_lease(self.ctx(), "b", "src/shared.py"))


if __name__ == "__main__":
    unittest.main()


class TestOneHarnessIdIsNotOneSession(RepoCase):
    """Four concurrent `claude -p` children collapsed into a single, incoherent record.

    They inherit `CLAUDE_CODE_SESSION_ID`, so every one of them reported the same
    `session_id` to every hook. Keyed on that alone, four sessions on four worktrees
    produced one record — worktree from the first, branch from the third, task statement
    from the second — and two of the four then read that task back as their own and
    rewrote a file they had never been asked to touch. Leases came out empty and every
    board said the session was alone on the repository.
    """

    def event(self, cwd, harness_id: str = "shared"):
        from claude_bestpractice.hookio import HookEvent

        return HookEvent({"session_id": harness_id, "cwd": str(cwd)})

    def test_two_worktrees_sharing_a_harness_id_are_two_sessions(self):
        other = self.add_worktree("sibling")
        self.assertNotEqual(self.event(self.repo).session_id, self.event(other).session_id)

    def test_the_same_worktree_is_the_same_session(self):
        """Resume and post-compaction restart must still find their own record."""
        self.assertEqual(self.event(self.repo).session_id, self.event(self.repo).session_id)

    def test_the_harness_id_still_separates_sessions_in_one_worktree(self):
        self.assertNotEqual(
            self.event(self.repo, "alpha").session_id, self.event(self.repo, "beta").session_id
        )

    def test_outside_a_repository_the_raw_id_is_kept(self):
        """Nothing to qualify against, and inventing a tag would be a lie about location."""
        self.assertEqual("shared", self.event(self.tmp / "not-a-repo").session_id)

    def test_each_worktree_keeps_its_own_task_statement(self):
        """The bug as the founder met it: another session's task read back as your own."""
        from claude_bestpractice import sessions

        other = self.add_worktree("second")
        for cwd, task in ((self.repo, "change billing.py"), (other, "change export.py")):
            self.run_hook("session-start", {"session_id": "shared",
                                            "hook_event_name": "SessionStart"}, cwd=cwd)
            self.run_hook("prompt-capture", {"session_id": "shared",
                                             "hook_event_name": "UserPromptSubmit",
                                             "prompt": task}, cwd=cwd)

        from claude_bestpractice.gitctx import resolve

        mine = sessions.get(resolve(self.repo), self.event(self.repo).session_id)
        theirs = sessions.get(resolve(other), self.event(other).session_id)
        self.assertIn("billing", mine.task_statement)
        self.assertIn("export", theirs.task_statement)


class TestTheReapLogIsBounded(RepoCase):
    """The one structure in Tier B that only ever grew.

    Measured across 400 sessions in one repository: every other file is rewritten or
    deleted, and this one appended ~200 bytes a session forever, with `reaped_memory`
    scanning all of it on every resume that finds no record.
    """

    def test_it_stops_growing(self):
        ctx = self.ctx()
        for i in range(sessions.REAPED_LOG_MAX + 40):
            sessions.register(ctx, record(ctx, f"dead{i}", pid=999_999_999))
        sessions.reap(ctx)
        kept = store.read_jsonl(store.tier_b(ctx, sessions.REAPED_LOG))
        self.assertLessEqual(len(kept), sessions.REAPED_LOG_MAX)

    def test_the_newest_reaps_are_the_ones_kept(self):
        """A session crashed a moment ago must still recover its baseline."""
        ctx = self.ctx()
        for i in range(sessions.REAPED_LOG_MAX + 10):
            sessions.register(ctx, record(ctx, f"old{i}", pid=999_999_999))
        sessions.reap(ctx)
        rec = record(ctx, "just-crashed", pid=999_999_999)
        rec.baseline_commit = "cafebabe"
        sessions.register(ctx, rec)
        sessions.reap(ctx)
        self.assertEqual("cafebabe", sessions.reaped_memory(ctx, "just-crashed").get("baseline_commit"))


class TestTheWatchedProcessIsTheRightOne(RepoCase):
    """The pid recorded for liveness was the hook's shell wrapper, not the CLI.

    Claude Code spawns hooks through `/bin/bash -c …`, which exits with the hook, so
    `os.getppid()` named a process that was dead milliseconds later. Every session then
    read every other as dead. Measured on a repository with three active chats: the
    board said `OTHER LIVE SESSIONS: none`, `reaped.jsonl` held 122 entries for 3 real
    sessions, and a file lease was released as soon as it was taken.

    The suite did not catch it for five releases because under test the hook's parent is
    the test runner, which stays alive for the assertion. So these tests spawn the way
    the harness does, and the record now carries how its pid was obtained.
    """

    def _shim(self, body: str) -> str:
        """Run `body` under a process actually named `claude`, one shell down.

        The ancestor has to genuinely be called `claude`, because that is what the walk
        reads: `/proc/<pid>/cmdline`, first two arguments, basename.

        It is a script rather than a relocated interpreter, and that is the whole point of
        this helper. Copying `sys.executable` to a new name is how the first version did
        it, and it broke `make check` for anyone running the project the ordinary way —
        under a virtualenv, a copied interpreter has no `pyvenv.cfg` beside it and no way
        back to its own stdlib, so it cannot start at all. Reported as issue #50, and the
        second failure of this shape in this suite. A shebang script needs nothing
        relocated: the kernel runs it as `sh <path-to-claude> …`, which puts the name in
        argv exactly where the walk looks for it.

        `exec` is deliberately absent. Exec'ing would replace the `claude`-named process
        with the interpreter and erase the very name being tested.
        """
        import os
        import subprocess
        import sys
        import textwrap

        shim = self.repo / "bin"
        shim.mkdir(exist_ok=True)
        claude = shim / "claude"
        claude.write_text(f'#!/bin/sh\n"{sys.executable}" "$@"\n', encoding="utf-8")
        claude.chmod(0o755)

        script = self.repo / "probe.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import subprocess, sys
                # One shell between the CLI and the hook, exactly as Claude Code runs
                # them. The trailing statement stops the shell exec'ing in place.
                print(subprocess.run(
                    ["sh", "-c", sys.executable + " -c " + repr({body!r}) + "; exit 0"],
                    capture_output=True, text=True,
                ).stdout, end="")
                """
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "PYTHONPATH": str(LIB)}
        out = subprocess.run(
            [str(claude), str(script)], capture_output=True, text=True, env=env, timeout=60
        )
        # Named separately from what the tests assert: a shim that cannot start is a
        # broken fixture, and reporting it as a failed liveness rule sends the reader
        # looking in the wrong file. That is precisely what issue #50 had to be diagnosed
        # through — `AssertionError: 0 != 1` over a stdlib that could not be found.
        self.assertEqual(0, out.returncode, f"the `claude` shim did not run: {out.stderr}")
        return out.stdout.strip()

    def test_the_owner_is_found_through_the_shell_that_spawned_the_hook(self):
        probe = (
            "import os,sys;from claude_bestpractice import sessions;"
            "pid,trust=sessions.resolve_owner();"
            "print(pid, trust, os.getppid())"
        )
        pid, trust, parent = self._shim(probe).split()
        self.assertEqual(sessions.PID_TRUST_OWNER, trust)
        self.assertNotEqual(parent, pid, "resolved the shell wrapper, which is the bug")

    def test_a_pid_that_was_never_resolved_to_the_cli_is_not_evidence_of_death(self):
        """This is the whole fix: an unattributed dead pid must not reap anything.

        Where the process tree cannot be read — anywhere without /proc, macOS included —
        the parent is all there is, and the parent is a wrapper that is *supposed* to be
        gone. Reading that as death is what made three live chats invisible to each other.
        """
        ctx = self.ctx()
        rec = record(ctx, "wrapper-gone", pid=999_999_999)
        rec.pid_trust = sessions.PID_TRUST_PARENT
        self.assertFalse(sessions.pid_alive(rec.pid))
        self.assertTrue(sessions.is_live(ctx, rec))

    def test_a_resolved_pid_still_decides_immediately(self):
        ctx = self.ctx()
        rec = record(ctx, "cli-gone", pid=999_999_999)
        self.assertEqual(sessions.PID_TRUST_OWNER, rec.pid_trust, "the fixture proves nothing")
        self.assertFalse(sessions.is_live(ctx, rec))

    def test_a_record_from_before_the_fix_is_retired_once_it_falls_silent(self):
        """Upgrading must clear the corpses the bug left, and only those.

        A record written by an older version carries a wrapper pid and no trust stamp.
        Honouring it forever would hand the founder a board full of phantom sessions for
        a day and a half after the upgrade; reaping it on sight would delete a session
        that is simply mid-think. It is retired only once it has also stopped
        heart-beating, which a genuinely live session never does for long.
        """
        ctx = self.ctx()
        legacy = record(ctx, "legacy", pid=999_999_999)
        legacy.pid_trust = ""
        self.assertTrue(sessions.is_live(ctx, legacy), "a fresh heartbeat outranks a wrapper pid")

        legacy.heartbeat_at = time.time() - (sessions.HEARTBEAT_STALE_SECONDS + 60)
        self.assertFalse(sessions.is_live(ctx, legacy))

    def test_a_live_session_re_stamps_its_own_legacy_record(self):
        """The upgrade path: one hook turns a pre-fix record into a trusted one."""
        ctx = self.ctx()
        identity = sid(self.repo, "legacy")
        legacy = record(ctx, identity, pid=999_999_999)
        legacy.pid_trust = ""
        sessions.register(ctx, legacy)

        self.run_hook("session-start", {"session_id": "legacy", "hook_event_name": "SessionStart"})

        after = sessions.get(ctx, identity)
        self.assertIn(after.pid_trust, (sessions.PID_TRUST_OWNER, sessions.PID_TRUST_PARENT))
        self.assertNotEqual(999_999_999, after.pid)


class TestALeaseSurvivesItsHooksExiting(RepoCase):
    """`pid_alive(holder['pid'])` released a lease as soon as it was taken.

    The contended-file refusal is one of this plugin's headline behaviours and it could
    not fire between two real chats, because the pid stamped on the lease was the hook's
    shell wrapper. The doctor check for it passed the whole time — it holds the lease
    inside one live process, where the wrapper happens to still exist.
    """

    def held_by_a_sibling(self, ttl: float) -> None:
        """Seed a lease taken by a session whose hook process is long gone."""
        ctx = self.ctx()
        with store.guarded_json(store.tier_b(ctx, sessions.LEASES_FILE), default={}) as box:
            box[0] = {
                sessions.lease_key(ctx, "src/x.py"): {
                    "session_id": "sibling",
                    "pid": 999_999_999,
                    "pid_trust": sessions.PID_TRUST_PARENT,
                    "acquired_at": time.time() - 1,
                    "expires_at": time.time() + ttl,
                }
            }

    def test_a_lease_stamped_with_an_unresolved_pid_still_holds(self):
        self.held_by_a_sibling(ttl=9999)
        self.assertEqual("sibling", sessions.acquire_lease(self.ctx(), "me", "src/x.py"))

    def test_an_expired_lease_is_still_taken_over(self):
        """The TTL is the half that was always load-bearing; it must keep working."""
        self.held_by_a_sibling(ttl=-10)
        self.assertIsNone(sessions.acquire_lease(self.ctx(), "me", "src/x.py"))


class TestTheGateJudgesTheTreeTheSessionWorksIn(RepoCase):
    """Issue #213. A session standing in the main checkout with its work in its own tree
    was judged where it stood: the Stop gate ran the suite, counted the diff and read the
    scope in a checkout the session was forbidden to write in and every sibling shares. It
    reported a failing test from somebody else's stale tree and 335 changed files belonging
    to nobody present, and the only way to clear it was to touch a tree this plugin's own
    rule says not to touch.

    Claude Code now reports the tree as the hook's directory once the session has `cd`-ed
    into it (2.1.280, 2.1.281), so the redirect is needed only while the session stands in
    the main checkout — and it must survive the session being a second identity in the tree.
    """

    def a_session_with_a_tree(self):
        from claude_bestpractice import worktree

        tree = self.add_worktree("work")
        worktree.record(self.ctx(), "work", str(tree), "work", True, session_id="s1")
        return tree

    def test_the_context_follows_the_session_into_its_tree(self):
        from claude_bestpractice import worktree

        tree = self.a_session_with_a_tree()
        moved = worktree.working_context(self.ctx(), "s1")
        self.assertEqual(tree.resolve(), moved.worktree_root)

    def test_a_session_with_no_tree_is_judged_where_it_stands(self):
        from claude_bestpractice import worktree

        self.a_session_with_a_tree()
        self.assertEqual(
            self.repo.resolve(),
            worktree.working_context(self.ctx(), "somebody-else").worktree_root,
        )

    def test_a_tree_git_no_longer_has_is_not_followed(self):
        """A guess about which checkout to judge is worse than judging the wrong one
        loudly: a removed tree leaves the context exactly where it was."""
        from claude_bestpractice import worktree
        from helpers import git

        tree = self.a_session_with_a_tree()
        git(["worktree", "remove", "--force", str(tree)], self.repo)
        self.assertEqual(
            self.repo.resolve(), worktree.working_context(self.ctx(), "s1").worktree_root
        )

    def one_session_in_both(self):
        """The tree made for the id the session had in the main checkout, and the session
        now registered under the tree's id as well — one process, as `resolve_owner` finds
        one CLI."""
        from claude_bestpractice import worktree
        from claude_bestpractice.gitctx import resolve

        tree = worktree.provision(self.ctx(), "the work", sid(self.repo, "s1"))
        for where in (self.repo, tree):
            sessions.register(resolve(where), record(resolve(where), sid(where, "s1")))
        return tree

    def test_standing_in_the_main_checkout_it_is_still_judged_in_its_tree(self):
        from claude_bestpractice import worktree

        tree = self.one_session_in_both()
        moved = worktree.working_context(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(tree.resolve(), moved.worktree_root)

    def test_standing_in_its_tree_it_is_judged_there(self):
        from claude_bestpractice import worktree
        from claude_bestpractice.gitctx import resolve

        tree = self.one_session_in_both()
        here = worktree.working_context(resolve(tree), sid(tree, "s1"))
        self.assertEqual(tree.resolve(), here.worktree_root)

    def test_standing_in_some_other_tree_it_is_judged_there_and_not_sent_back(self):
        """Known under every id it has had, a session in a tree it went into by itself
        would otherwise be judged in the tree it was handed and left."""
        from claude_bestpractice import worktree
        from claude_bestpractice.gitctx import resolve

        self.one_session_in_both()
        elsewhere = self.add_worktree("elsewhere")
        sessions.register(resolve(elsewhere), record(resolve(elsewhere), sid(elsewhere, "s1")))
        judged = worktree.working_context(resolve(elsewhere), sid(elsewhere, "s1"))
        self.assertEqual(elsewhere.resolve(), judged.worktree_root)


class TestTheDiffIsWhatThisSessionChanged(RepoCase):
    """The other half of #213: every uncommitted change in the tree was counted as this
    session's, including files another session had left mid-edit before it started. In a
    main checkout shared by three to eight sessions that is somebody else's work — and it
    arrived as a scope-drift refusal and a demand for a card over 335 files the session
    had never opened.
    """

    def a_neighbour_mid_edit(self) -> str:
        """A tracked file another session is part-way through, and our baseline over it."""
        from claude_bestpractice.gitctx import stash_baseline

        self.write("README.md", "seed\n# another session was mid-edit here\n")
        return stash_baseline(self.ctx())

    def test_state_that_was_already_dirty_at_the_baseline_is_not_this_session_s(self):
        from claude_bestpractice.gitctx import changed_files

        self.assertEqual([], changed_files(self.ctx(), self.a_neighbour_mid_edit()))

    def test_what_the_session_does_change_is_still_counted(self):
        from claude_bestpractice.gitctx import changed_files

        baseline = self.a_neighbour_mid_edit()
        self.write("src/mine.py", "print('mine')\n")
        self.assertEqual(["src/mine.py"], changed_files(self.ctx(), baseline))

    def test_a_file_the_session_edits_further_is_counted(self):
        """The line between the two: it was dirty before, and this session moved it
        again."""
        from claude_bestpractice.gitctx import changed_files

        baseline = self.a_neighbour_mid_edit()
        self.write("README.md", "seed\n# and then this session\n")
        self.assertEqual(["README.md"], changed_files(self.ctx(), baseline))

    def test_a_commit_this_session_made_is_counted(self):
        from claude_bestpractice.gitctx import changed_files

        baseline = self.a_neighbour_mid_edit()
        self.write("src/mine.py", "print('mine')\n")
        self.commit("this session's work")
        self.assertIn("src/mine.py", changed_files(self.ctx(), baseline))


def backdate(ctx, session_id: str, seconds: float) -> None:
    """Make a registered session's last heartbeat `seconds` old, as silence would."""
    path = store.tier_b(ctx, sessions.SESSIONS_DIR, f"{sessions.safe_id(session_id)}.json")
    raw = store.read_json(path)
    raw["heartbeat_at"] = time.time() - seconds
    store.write_json(path, raw)


class TestAWeekendAwayIsNotADeath(RepoCase):
    """A session left open over a weekend was reaped by the first one started on Monday.

    The heartbeat ceiling was checked after the pid proof and overruled it: a CLI still
    running, resolved as the owner, with the start time it registered with, was declared
    dead at 36 hours of silence — and the sweep that follows a reap removed its tree while
    the CLI was standing in it. The ceiling is the backstop for a pid that cannot speak
    for itself. That one could.
    """

    def a_cli_left_running(self, harness_id: str) -> subprocess.Popen:
        """A process named `claude` that registers through the real SessionStart, one shell
        down as the harness runs hooks, and then simply stays up."""
        home = self.tmp / "cli"
        home.mkdir(exist_ok=True)
        cli = home / "claude"
        cli.write_text(f'#!/bin/sh\n"{sys.executable}" "$@"\n', encoding="utf-8")
        cli.chmod(0o755)
        event = {"session_id": harness_id, "cwd": str(self.repo),
                 "hook_event_name": "SessionStart", "source": "startup"}
        stays_up = home / "session.py"
        stays_up.write_text(
            "import subprocess, sys, time\n"
            f"hook = sys.executable + ' ' + {str(BIN / 'session-start')!r} + '; exit 0'\n"
            f"subprocess.run(['sh', '-c', hook], input={json.dumps(event)!r}, text=True,"
            " capture_output=True)\n"
            "print('registered', flush=True)\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        proc = subprocess.Popen([str(cli), str(stays_up)], stdout=subprocess.PIPE, text=True,
                                env={**os.environ, "PYTHONPATH": str(LIB)}, start_new_session=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        self.addCleanup(os.killpg, proc.pid, signal.SIGKILL)
        self.assertEqual("registered", proc.stdout.readline().strip(), "the stand-in CLI never ran")
        return proc

    @unittest.skipUnless(os.path.isdir("/proc/self"), "the owner walk reads /proc")
    def test_a_cli_quiet_past_the_ceiling_keeps_its_record_and_its_tree(self):
        cli = self.a_cli_left_running("weekend")
        ctx = self.ctx()
        me = sid(self.repo, "weekend")
        rec = sessions.get(ctx, me)
        self.assertEqual((cli.pid, sessions.PID_TRUST_OWNER), (rec.pid, rec.pid_trust),
                         "precondition: the record names the CLI itself")
        tree = worktree.provision(ctx, "the csv export", me)
        backdate(ctx, me, sessions.HEARTBEAT_DEAD_SECONDS + 27 * 3600)

        self.run_hook("session-start", {"session_id": "monday", "hook_event_name": "SessionStart",
                                        "source": "startup"})

        self.assertIsNotNone(sessions.get(ctx, me), "a running session was reaped")
        self.assertTrue(tree.is_dir(), "its tree was removed with the CLI standing in it")

    def test_a_record_that_outlived_a_reboot_still_falls_to_the_ceiling(self):
        """The backstop the ceiling was kept for: after a reboot the same pid, even with a
        matching start time, can be a stranger."""
        ctx = self.ctx()
        sessions.register(ctx, record(ctx, "before-the-reboot"))
        rec = sessions.get(ctx, "before-the-reboot")
        if not (rec.boot_id and rec.pid_fingerprint):
            self.skipTest("the kernel names neither the boot nor a start time here")
        rec.heartbeat_at = time.time() - (sessions.HEARTBEAT_DEAD_SECONDS + 60)
        self.assertTrue(sessions.is_live(ctx, rec), "precondition: on this boot it is live")

        rec.boot_id = "a-boot-that-is-over"
        self.assertFalse(sessions.is_live(ctx, rec))


class TestAPidFromAnotherNamespaceProvesNothing(RepoCase):
    """A session in a container sharing the checkout was reaped by one on the host.

    Its record said pid 2, owner — and on the host pid 2 is `kthreadd`, whose start time
    does not match, which read as a recycled pid: positive evidence of death. The board
    said `OTHER LIVE SESSIONS: none`, and the sweep removed the tree the container session
    was still working in. `store.lock_identity` already guarded the locks against exactly
    this; the registry never asked.
    """

    ELSEWHERE = "another-host/pid:[4026532263]"

    def a_session_in_a_container(self, ctx) -> sessions.SessionRecord:
        """Registered in another pid namespace, under a pid that here is nobody."""
        sessions.register(ctx, record(ctx, "in-a-container"))
        rec = sessions.get(ctx, "in-a-container")
        rec.pid, rec.pid_fingerprint, rec.pid_identity = 999_999_999, "4242", self.ELSEWHERE
        store.write_json(store.tier_b(ctx, sessions.SESSIONS_DIR, "in-a-container.json"),
                         rec.to_dict())
        return rec

    def test_it_is_live_from_here(self):
        ctx = self.ctx()
        self.assertTrue(sessions.is_live(ctx, self.a_session_in_a_container(ctx)))

    def test_the_host_s_session_start_leaves_it_and_its_tree_alone(self):
        ctx = self.ctx()
        container = self.a_session_in_a_container(ctx)
        tree = worktree.provision(ctx, "rate limiting", container.session_id)

        self.run_hook("session-start", {"session_id": "on-the-host",
                                        "hook_event_name": "SessionStart", "source": "startup"})

        self.assertIsNotNone(sessions.get(ctx, container.session_id), "reaped from the host")
        self.assertTrue(tree.is_dir(), "its tree was removed while it was working in it")

    def test_the_ceiling_is_still_its_backstop(self):
        ctx = self.ctx()
        rec = self.a_session_in_a_container(ctx)
        rec.heartbeat_at = time.time() - (sessions.HEARTBEAT_DEAD_SECONDS + 60)
        self.assertFalse(sessions.is_live(ctx, rec))


class TestAResumeDuringAReapStillFindsItsBaseline(RepoCase):
    def test_the_log_entry_is_written_before_the_record_goes(self):
        """Unlinked first, a resume landing between the two found neither the record nor
        the entry, re-anchored at HEAD, and every commit before the crash left the diff."""
        ctx = self.ctx()
        sessions.register(ctx, record(ctx, "crashed", pid=999_999_999))
        seen = []
        logged = store.append_jsonl

        def watching(path, row, mode=0o600):
            seen.append(sessions.get(ctx, "crashed"))
            logged(path, row, mode)

        with mock.patch.object(store, "append_jsonl", watching):
            sessions.reap(ctx)
        self.assertIsNotNone(seen[0], "the record was gone before its baseline was logged")
        self.assertIsNone(sessions.get(ctx, "crashed"))
