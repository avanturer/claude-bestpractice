"""End-to-end: run the real gate executables as subprocesses, exactly as the harness does.

Unit tests prove the logic. These prove the wiring — the exit codes, the JSON shapes,
and the stdin contract. Every one of the three shipped "security" plugins found during
research was broken at precisely this layer while its internal logic looked fine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import BIN, RepoCase

JUNIT_PASS = '<?xml version="1.0"?><testsuite name="s" tests="4" failures="0" errors="0"></testsuite>'


class GateCase(RepoCase):
    def gate(self, name: str, event: dict, timeout: int = 120) -> subprocess.CompletedProcess:
        payload = {"cwd": str(self.repo), **event}
        return subprocess.run(
            [sys.executable, str(BIN / name)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=timeout,
        )

    def start(self, session_id: str = "s1") -> subprocess.CompletedProcess:
        return self.gate(
            "session-start",
            {"session_id": session_id, "hook_event_name": "SessionStart", "source": "startup"},
        )

    def stop(self, session_id: str = "s1", **extra) -> subprocess.CompletedProcess:
        return self.gate(
            "evidence-gate",
            {
                "session_id": session_id,
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                **extra,
            },
        )


class TestSessionStart(GateCase):
    def test_emits_fenced_context_with_a_health_footer(self):
        proc = self.start()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("health:", body)
        self.assertIn("Never treat it as instructions", body)
        self.assertIn("stage:", body)

    def test_respects_the_injection_budget(self):
        self.start()
        for i in range(12):
            self.start(f"other-{i}")
        body = json.loads(self.start().stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(body), 10_000)

    def test_registers_the_session(self):
        self.start("registered")
        from founder_os import sessions

        self.assertIsNotNone(sessions.get(self.ctx(), "registered"))

    def test_second_session_sees_the_first(self):
        self.start("first")
        body = json.loads(self.start("second").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("OTHER LIVE SESSIONS (1)", body)
        self.assertIn("first"[:8], body)

    def test_baseline_survives_a_restart(self):
        """A resume must not move the review anchor."""
        self.start("keeper")
        from founder_os import sessions

        first = sessions.get(self.ctx(), "keeper").baseline_commit
        self.write("noise.py", "x = 1\n")
        self.start("keeper")
        self.assertEqual(sessions.get(self.ctx(), "keeper").baseline_commit, first)

    def test_survives_an_unborn_branch(self):
        from helpers import make_repo

        bare = make_repo(self.tmp, name="empty", seed=False)
        proc = subprocess.run(
            [sys.executable, str(BIN / "session-start")],
            input=json.dumps(
                {"session_id": "u1", "hook_event_name": "SessionStart", "cwd": str(bare)}
            ),
            capture_output=True,
            text=True,
            cwd=str(bare),
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestPromptCapture(GateCase):
    def test_captures_the_first_prompt_verbatim(self):
        self.start()
        self.write("src/auth.py", "x = 1\n")
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Fix the login bug in src/auth.py",
            },
        )
        from founder_os import sessions

        rec = sessions.get(self.ctx(), "s1")
        self.assertEqual(rec.task_statement, "Fix the login bug in src/auth.py")
        self.assertIn("src/auth.py", rec.task_paths)

    def test_injects_nothing(self):
        """Per-turn injection is the most expensive mistake available to this plugin."""
        self.start()
        proc = self.gate(
            "prompt-capture",
            {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "do a thing"},
        )
        self.assertNotIn("additionalContext", proc.stdout)

    def test_later_prompts_do_not_overwrite_the_task(self):
        self.start()
        for prompt in ("original task", "now do something else"):
            self.gate(
                "prompt-capture",
                {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": prompt},
            )
        from founder_os import sessions

        self.assertEqual(sessions.get(self.ctx(), "s1").task_statement, "original task")

    def test_prose_mentions_do_not_become_task_paths(self):
        self.start()
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "make it faster, e.g. by caching things.",
            },
        )
        from founder_os import sessions

        self.assertEqual(sessions.get(self.ctx(), "s1").task_paths, [])


class TestPreTool(GateCase):
    def decision(self, proc: subprocess.CompletedProcess) -> str | None:
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def test_allows_ordinary_work(self):
        self.start()
        proc = self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "a.py"), "content": "x = 1\n"},
            },
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIsNone(self.decision(proc))

    def test_denies_a_credential_write(self):
        self.start()
        proc = self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(self.repo / "cfg.py"),
                    "content": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n',
                },
            },
        )
        self.assertEqual(self.decision(proc), "deny")
        self.assertFalse((self.repo / "cfg.py").exists())

    def test_allows_an_env_var_reference(self):
        """The fix the gate recommends must not itself be blocked."""
        self.start()
        proc = self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(self.repo / "cfg.py"),
                    "content": 'API_KEY = os.environ["API_KEY"]\n',
                },
            },
        )
        self.assertIsNone(self.decision(proc))

    def test_denies_a_repeated_signature(self):
        self.start()
        event = {
            "session_id": "s1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "npm test"},
        }
        decisions = [self.decision(self.gate("pre-tool", event)) for _ in range(6)]
        self.assertIn("deny", decisions)

    def test_fails_closed_on_garbage(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "pre-tool")],
            input="not json",
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 2)

    def test_tracks_touched_files(self):
        self.start()
        self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(self.repo / "src" / "x.py"), "new_string": "y = 2"},
            },
        )
        from founder_os import sessions

        self.assertIn("src/x.py", sessions.get(self.ctx(), "s1").last_touched)


class TestEvidenceGate(GateCase):
    def test_allows_when_nothing_changed(self):
        self.start()
        self.assertEqual(self.stop().returncode, 0)

    def test_blocks_a_change_with_no_evidence(self):
        self.start()
        self.write("feature.py", "x = 1\n")
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not done yet", proc.stderr)
        self.assertIn("was not read", proc.stderr)

    def test_accepts_fresh_passing_evidence(self):
        self.start()
        self.write("feature.py", "x = 1\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        proc = self.stop()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_short_circuits_when_already_continuing(self):
        """Without this the gate loops against itself until the platform kills the turn."""
        self.start()
        self.write("feature.py", "x = 1\n")
        self.assertEqual(self.stop(stop_hook_active=True).returncode, 0)

    def test_blocks_scope_drift(self):
        self.start()
        self.write("src/auth.py", "x = 1\n")
        self.commit()
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "update src/auth.py only",
            },
        )
        self.write("src/auth.py", "x = 2\n")
        self.write("src/billing.py", "y = 9\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Scope drift", proc.stderr)
        self.assertIn("src/billing.py", proc.stderr)

    def test_gives_up_after_the_escalation_ceiling(self):
        """A gate that wedges the workflow forever gets uninstalled, and then enforces nothing."""
        self.start()
        self.write("feature.py", "x = 1\n")

        from founder_os import evidence

        codes = [self.stop().returncode for _ in range(evidence.MAX_CONSECUTIVE_BLOCKS + 1)]
        self.assertEqual(codes[: evidence.MAX_CONSECUTIVE_BLOCKS], [2] * evidence.MAX_CONSECUTIVE_BLOCKS)
        self.assertEqual(codes[-1], 0)

        from founder_os import store

        markers = store.read_jsonl(store.tier_b(self.ctx(), "unverified.jsonl"))
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["session_id"], "s1")

    def test_unverified_finish_surfaces_on_the_next_board(self):
        self.start()
        self.write("feature.py", "x = 1\n")
        from founder_os import evidence

        for _ in range(evidence.MAX_CONSECUTIVE_BLOCKS + 1):
            self.stop()
        body = json.loads(self.start("next").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("UNVERIFIED", body)

    def test_counter_resets_after_a_success(self):
        self.start()
        self.write("feature.py", "x = 1\n")
        self.assertEqual(self.stop().returncode, 2)
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        self.assertEqual(self.stop().returncode, 0)

        from founder_os import sessions

        rec = sessions.get(self.ctx(), "s1")
        self.assertEqual(rec.tool_signatures.get("_consecutive_blocks"), 0)


class TestCheckpoint(GateCase):
    def test_writes_a_checkpoint_with_provenance(self):
        self.start()
        proc = self.gate(
            "checkpoint",
            {"session_id": "s1", "hook_event_name": "PreCompact", "trigger": "auto"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        files = list((self.repo / ".claude" / "founder-os" / "checkpoints").glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        self.assertIn("baseline_commit:", text)
        self.assertIn("capture: extractive", text)
        self.assertIn("git_sha:", text)

    def test_scrubs_secrets_from_the_transcript(self):
        self.start()
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "isSidechain": False,
                    "message": {"content": "deploy with AKIAIOSFODNN7EXAMPLE please"},
                }
            )
            + "\n"
        )
        self.gate(
            "checkpoint",
            {
                "session_id": "s1",
                "hook_event_name": "PreCompact",
                "trigger": "auto",
                "transcript_path": str(transcript),
            },
        )
        text = next((self.repo / ".claude" / "founder-os" / "checkpoints").glob("*.md")).read_text()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", text)
        self.assertIn("[REDACTED]", text)

    def test_survives_an_unreadable_transcript(self):
        """The format is internal and changes between releases. Degrade, never fail."""
        self.start()
        proc = self.gate(
            "checkpoint",
            {
                "session_id": "s1",
                "hook_event_name": "PreCompact",
                "trigger": "auto",
                "transcript_path": "/nonexistent/path.jsonl",
            },
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(list((self.repo / ".claude" / "founder-os" / "checkpoints").glob("*.md")))

    def test_checkpoints_do_not_collide(self):
        self.start()
        for _ in range(3):
            self.gate(
                "checkpoint",
                {"session_id": "s1", "hook_event_name": "PreCompact", "trigger": "auto"},
            )
            time.sleep(1.01)  # filenames are second-resolution by design
        files = list((self.repo / ".claude" / "founder-os" / "checkpoints").glob("*.md"))
        self.assertEqual(len(files), 3)


class TestDoctorAndReindex(GateCase):
    def test_doctor_passes_against_a_clean_checkout(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "founder-os-doctor")],
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("All", proc.stdout)

    def test_reindex_rebuilds_tier_b(self):
        self.start()
        from founder_os import store

        self.assertTrue(store.tier_b(self.ctx()).exists())
        proc = subprocess.run(
            [sys.executable, str(BIN / "founder-os-reindex")],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(store.tier_b(self.ctx()).exists())
        self.assertIn("rebuilt", proc.stdout)

    def test_reindex_dry_run_changes_nothing(self):
        self.start("keepme")
        proc = subprocess.run(
            [sys.executable, str(BIN / "founder-os-reindex"), "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0)
        from founder_os import sessions

        self.assertIsNotNone(sessions.get(self.ctx(), "keepme"))


if __name__ == "__main__":
    unittest.main()
