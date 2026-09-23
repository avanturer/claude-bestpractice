"""The dead-end ledger: the half of memory that a successful revert destroys."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase

from claude_bestpractice import attempts


def _decision(proc) -> str:
    """The gate's verdict, defaulting to allow — silence is how it permits."""
    try:
        return json.loads(proc.stdout)["hookSpecificOutput"].get("permissionDecision", "allow")
    except (json.JSONDecodeError, KeyError, TypeError):
        return "allow"


class AttemptCase(RepoCase):
    def ctx(self):
        from claude_bestpractice.gitctx import resolve

        return resolve(self.repo)

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-attempt"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )


class TestRecording(AttemptCase):
    def test_an_attempt_survives_as_committed_state(self):
        from claude_bestpractice import attempts

        attempts.record(self.ctx(), "websockets", "reconnect storms", ["src/ws.ts"])
        files = list((self.repo / ".claude" / "claude-bestpractice" / "attempts").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("reconnect storms", files[0].read_text())

    def test_the_same_dead_end_is_not_filed_twice(self):
        """A ledger that accumulates near-duplicates is one nobody reads."""
        from claude_bestpractice import attempts

        ctx = self.ctx()
        self.assertIsNotNone(attempts.record(ctx, "websockets", "reconnect storms", ["src/ws.ts"]))
        self.assertIsNone(attempts.record(ctx, "WebSockets", "again, same thing", ["src/ws.ts"]))
        self.assertEqual(len(attempts.load_all(ctx)), 1)

    def test_the_same_title_on_different_files_is_a_different_attempt(self):
        from claude_bestpractice import attempts

        ctx = self.ctx()
        attempts.record(ctx, "polling", "too slow", ["src/a.ts"])
        attempts.record(ctx, "polling", "too slow", ["src/b.ts"])
        self.assertEqual(len(attempts.load_all(ctx)), 2)

    def test_ids_do_not_collide_under_parallel_writes(self):
        workers = [
            subprocess.Popen(
                [sys.executable, str(BIN / "claude-bp-attempt"), "add", f"approach {i}",
                 "--why", "did not work", "--paths", f"src/{i}.ts"],
                cwd=str(self.repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for i in range(8)
        ]
        for worker in workers:
            worker.wait(timeout=180)
        files = list((self.repo / ".claude" / "claude-bestpractice" / "attempts").glob("*.md"))
        ids = [f.name.split("-", 1)[0] for f in files]
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8, f"duplicate ids: {sorted(ids)}")


class TestSurfacing(AttemptCase):
    """By subject, not by recency — otherwise it is archaeology in every turn."""

    def test_it_warns_only_on_the_files_being_touched(self):
        from claude_bestpractice import attempts

        ctx = self.ctx()
        attempts.record(ctx, "websockets", "reconnect storms", ["src/ws.ts"])
        self.assertIn("websockets", attempts.render_for_board(ctx, ["src/ws.ts"]))
        self.assertEqual(attempts.render_for_board(ctx, ["src/landing.tsx"]), "")

    def test_every_file_it_names_is_its_subject_not_only_the_first(self):
        """The paths are written joined by ", ", and every one after the first was read back
        with a space in front: a dead end about two files warned on the first alone, and
        the same one on the second was filed again as new."""
        ctx = self.ctx()
        attempts.record(ctx, "websockets", "reconnect storms", ["src/client.ts", "src/ws.ts"])
        self.assertIn("websockets", attempts.render_for_board(ctx, ["src/ws.ts"]))
        self.assertIsNone(attempts.record(ctx, "websockets", "again", ["src/ws.ts"]))

    def test_no_subject_at_all_falls_back_to_the_most_recent(self):
        """At SessionStart there is no subject — and `paths` was empty EVERY time.

        Returning nothing there meant the ledger never reached a session that was not
        present when the dead end was hit, which is the only reason this file exists. The
        dead ends were recorded, committed, and read by nobody, silently.

        An unmatched subject still says nothing (the test above): that is the case where
        recency would be actively wrong. An ABSENT subject is different — there is nothing
        to be irrelevant to yet, and the alternative is saying nothing at all.
        """
        from claude_bestpractice import attempts

        ctx = self.ctx()
        attempts.record(ctx, "websockets", "reconnect storms", ["src/ws.ts"])
        self.assertIn("websockets", attempts.render_for_board(ctx, []))

    def test_a_new_session_is_told_what_was_already_tried(self):
        """The same thing again, through the gate the founder actually runs."""
        import json

        from claude_bestpractice import attempts

        attempts.record(
            self.ctx(), "in-house stripe proration", "disagreed with stripe by cents", ["billing.py"]
        )
        proc = self.run_hook(
            "session-start",
            {"session_id": "newcomer", "hook_event_name": "SessionStart", "cwd": str(self.repo)},
        )
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("stripe proration", body)

    def test_the_board_carries_the_warning_into_the_next_session(self):
        from claude_bestpractice import attempts

        attempts.record(self.ctx(), "websockets", "reconnect storms", ["src/ws.ts"])
        self.write("src/ws.ts", "// editing this again\n")
        self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "src" / "ws.ts"), "content": "x"},
            },
        )
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ALREADY TRIED", body)
        self.assertIn("websockets", body)


class TestAutomaticCapture(AttemptCase):
    """Manual upkeep is the problem this replaces, so most of it must fill itself."""

    def test_an_unverified_finish_becomes_a_failed_attempt(self):
        from claude_bestpractice import attempts

        self.write("app.py", "x = 1\n")
        self.commit()
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.run_hook(
            "prompt-capture",
            {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "rewrite app.py"},
        )
        self.write("app.py", "x = 2\n")

        codes = [
            self.run_hook(
                "evidence-gate",
                {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
            ).returncode
            for _ in range(6)
        ]
        self.assertIn(0, codes, f"never escalated: {codes}")

        filed = attempts.load_all(self.ctx())
        self.assertTrue(filed, "an unverified finish left no trace for the next session")
        self.assertEqual(filed[0].outcome, attempts.FAILED)
        self.assertIn("app.py", filed[0].paths)


class TestCLI(AttemptCase):
    def test_add_then_list(self):
        add = self.cli("add", "websockets", "--why", "reconnect storms", "--paths", "src/ws.ts")
        self.assertEqual(add.returncode, 0, add.stderr)
        listing = self.cli("list")
        self.assertIn("websockets", listing.stdout)
        self.assertIn("reconnect storms", listing.stdout)

    def test_list_scoped_to_paths(self):
        self.cli("add", "websockets", "--why", "storms", "--paths", "src/ws.ts")
        self.cli("add", "gradients", "--why", "looked generated", "--paths", "src/landing.tsx")
        scoped = self.cli("list", "--paths", "src/ws.ts")
        self.assertIn("websockets", scoped.stdout)
        self.assertNotIn("gradients", scoped.stdout)

    def test_empty_ledger_says_so(self):
        self.assertIn("nothing tried", self.cli("list").stdout)


if __name__ == "__main__":
    unittest.main()


class TestADeadEndAboutRewrittenCodeIsMarked(RepoCase):
    """The stamp was written at record time and read by nobody.

    Verified on a real repository: `svc/fees.py` rewritten from floats to per-tenant
    `Decimal`, and the board went on asserting that caching rates in a dict leaks across
    tenants — advice about code that no longer exists, presented as current.

    Suppressing it outright would be the wrong correction. "We tried X and it failed
    because Y" stays true whatever happens to the file afterwards. What decays is its
    bearing on the code in front of you, so that is what is said.
    """

    def test_a_rewritten_subject_is_marked_and_kept(self):
        from claude_bestpractice import attempts

        self.write("svc/fees.py", "def rate(c):\n    return c * 0.02\n")
        self.commit()
        attempts.record(self.ctx(), "Cache rates in a dict", "leaked across tenants",
                        ["svc/fees.py"], outcome="failed", session_id="s1")

        self.assertNotIn("may no longer apply", attempts.render_for_board(self.ctx(), []))

        self.write("svc/fees.py", "from decimal import Decimal\n\n\ndef rate(c, t):\n    return Decimal(c)\n")
        self.commit()
        board = attempts.render_for_board(self.ctx(), [])
        self.assertIn("Cache rates in a dict", board, "history was dropped, not marked")
        self.assertIn("may no longer apply", board)
        self.assertIn("svc/fees.py", board)

    def test_an_untouched_subject_is_not_marked(self):
        """A false mark is as bad as a missing one: it teaches the reader to ignore both."""
        from claude_bestpractice import attempts

        self.write("svc/other.py", "X = 1\n")
        self.write("svc/fees.py", "Y = 1\n")
        self.commit()
        attempts.record(self.ctx(), "Tried a thing", "did not work",
                        ["svc/other.py"], outcome="failed", session_id="s1")
        self.write("svc/fees.py", "Y = 2\n")
        self.commit()
        self.assertNotIn("may no longer apply", attempts.render_for_board(self.ctx(), []))

    def test_a_deleted_subject_says_so(self):
        from claude_bestpractice import attempts

        self.write("svc/gone.py", "X = 1\n")
        self.commit()
        attempts.record(self.ctx(), "Tried in gone.py", "did not work",
                        ["svc/gone.py"], outcome="failed", session_id="s1")
        (self.repo / "svc" / "gone.py").unlink()
        self.commit()
        self.assertIn("kept as history", attempts.render_for_board(self.ctx(), []))


class TestTwoWorktreesFileTwoDeadEnds(RepoCase):
    """Ids were counted per tree and stamps were keyed by id. Two worktrees each filed
    `0001`, the one `attempt-0001.stamp` held whichever came last, and a dead end about a
    rewritten `billing.py` was shown as current advice because its stamp named
    `landing.html`. After the merge `drop 0001` retired whichever file came first.
    """

    def setUp(self) -> None:
        super().setUp()
        self.write("billing.py", "rates = {}\n")
        self.write("landing.html", "<h1>hi</h1>\n")
        self.commit("the files")
        self.a = self.add_worktree("billing")
        self.b = self.add_worktree("landing")

    def file(self, tree, title: str, path: str):
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-attempt"), "add", title,
             "--why", "did not work", "--paths", path],
            capture_output=True, text=True, cwd=str(tree), timeout=180,
        )

    def test_each_gets_its_own_id_and_its_own_stamp(self):
        from claude_bestpractice.gitctx import resolve

        self.file(self.a, "cache fee rates in a module dict", "billing.py")
        self.file(self.b, "hero video autoplay", "landing.html")
        ids = [a.id for tree in (self.a, self.b) for a in attempts.load_all(resolve(tree))]
        self.assertEqual(2, len(set(ids)), ids)

        (self.a / "billing.py").write_text("rates = {}  # per tenant now\n", encoding="utf-8")
        self.assertIn("rewritten since", attempts.render_for_board(resolve(self.a), ["billing.py"]))
        self.assertNotIn("rewritten since",
                         attempts.render_for_board(resolve(self.b), ["landing.html"]))

    def test_a_number_that_comes_round_again_does_not_share_a_stamp(self):
        """Allocation can only see the trees that exist. One removed before its branch
        merged takes its attempts out of sight, so its number is handed out again — and
        keyed by that number, the second stamp replaced the first."""
        from helpers import git

        self.file(self.a, "cache fee rates in a module dict", "billing.py")
        git(["add", "-A"], self.a)
        git(["commit", "-qm", "the dead end"], self.a)
        git(["worktree", "remove", "--force", str(self.a)], self.repo)
        self.file(self.b, "hero video autoplay", "landing.html")
        git(["merge", "-q", "--no-edit", "billing"], self.repo)

        self.write("billing.py", "rates = {}  # per tenant now\n")
        self.assertIn("rewritten since", attempts.render_for_board(self.ctx(), ["billing.py"]))

    def test_a_stamp_an_older_version_keyed_by_id_is_read_only_for_its_own_files(self):
        """Stamps already on disk keep working — unless a sibling's overwrote them, and
        then they say nothing rather than something about somebody else's file."""
        from claude_bestpractice import store
        from claude_bestpractice.gitctx import resolve

        ctx = resolve(self.a)
        attempt = attempts.record(ctx, "cache fee rates in a module dict", "leaks",
                                  ["billing.py"])
        store.tier_b(ctx, f"attempt-{attempt.path.stem}.stamp").unlink()
        legacy = store.tier_b(ctx, f"attempt-{attempt.id}.stamp")

        store.write_json(legacy, [{"path": "landing.html", "blob": "0" * 40}])
        self.assertNotIn("rewritten since", attempts.render_for_board(ctx, ["billing.py"]))
        store.write_json(legacy, [{"path": "billing.py", "blob": "0" * 40}])
        self.assertIn("billing.py rewritten since", attempts.render_for_board(ctx, ["billing.py"]))


class TestAnAttemptCanBeRetired(RepoCase):
    """Issue #92. Every other layer here can be retired — decisions, curated documents,
    review findings, worktrees. Attempts were the one write-only ledger.

    Both entries in the reporting repository described failures that never happened: they
    were filed by the scope-drift defect closed in #71, then sat in ALREADY TRIED on every
    session start, warning about things nobody had failed at.
    """

    def filed(self) -> str:
        entry = attempts.record(
            self.ctx(), "a thing that did not work", "because of a defect since fixed",
            ["src/app.py"], outcome="failed", branch="main",
        )
        self.assertIsNotNone(entry)
        return entry.id

    def test_it_can_be_dropped_by_id(self):
        attempt_id = self.filed()
        self.assertTrue(attempts.load_all(self.ctx()))

        removed = attempts.drop(self.ctx(), attempt_id)
        self.assertTrue(removed)
        self.assertEqual([], attempts.load_all(self.ctx()))

    def test_dropping_one_that_is_not_there_says_so(self):
        self.assertEqual("", attempts.drop(self.ctx(), "9999"))

    def test_dropping_leaves_the_others(self):
        first = self.filed()
        attempts.record(
            self.ctx(), "a different thing", "for a different reason entirely",
            ["src/other.py"], outcome="failed", branch="main",
        )
        attempts.drop(self.ctx(), first)
        self.assertEqual(1, len(attempts.load_all(self.ctx())))


class TestTheLedgerIsCleanableWhereItLives(RepoCase):
    relax_git_policy = False

    """The files are untracked and live in the main checkout, so the worktree gate refused
    the deletion and offered a tree that could not hold them — #68's dead end, reached from
    the untracked side rather than the ignored one."""

    def test_deleting_an_untracked_file_in_the_main_checkout_is_allowed(self):
        import json as _json
        import subprocess as _sp
        import sys as _sys

        from helpers import BIN

        (self.repo / "scratch.md").write_text("untracked, lives only here\n", encoding="utf-8")
        proc = _sp.run(
            [_sys.executable, str(BIN / "pre-tool")],
            input=_json.dumps({
                "cwd": str(self.repo), "session_id": "s1", "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": f"rm -f {self.repo / 'scratch.md'}"},
            }),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )
        # Silence IS allow for this gate, which is how every other test here reads it.
        self.assertNotEqual("deny", _decision(proc), proc.stdout[:300])

    def test_a_tracked_file_is_still_refused(self):
        import json as _json
        import subprocess as _sp
        import sys as _sys

        from helpers import BIN

        proc = _sp.run(
            [_sys.executable, str(BIN / "pre-tool")],
            input=_json.dumps({
                "cwd": str(self.repo), "session_id": "s1", "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": f"rm -f {self.repo / 'README.md'}"},
            }),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )
        self.assertEqual("deny", _decision(proc))
