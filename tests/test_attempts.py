"""The dead-end ledger: the half of memory that a successful revert destroys."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase


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
