"""The ways enforcement was found to switch itself off, each one now a test.

Independent adversarial verification of v1.0 confirmed ten blockers. Every one shared a
shape: the gate did not crash, did not warn, and did not enforce. A gate that fails
loudly gets fixed on the first run; a gate that fails open and silent is indistinguishable
from a gate that is working, which is the exact condition this project exists to prevent.

So these tests do not check that the code takes a particular branch. They perform the
attack and assert the outcome, because every one of these bugs was invisible to reading.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import BIN, RepoCase

SECRET = 'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'


class DisarmCase(RepoCase):
    def start(self, session_id: str = "s1"):
        return self.run_hook(
            "session-start", {"session_id": session_id, "hook_event_name": "SessionStart"}
        )

    def secret_write_denied(self, session_id: str = "s1") -> bool:
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": session_id,
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "k.py"), "content": SECRET},
            },
        )
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    def session_files(self) -> list[Path]:
        directory = self.repo / ".git" / "founder-os" / "sessions"
        return sorted(directory.glob("*.json")) if directory.is_dir() else []

    def backdate(self, seconds: float) -> None:
        for path in self.session_files():
            record = json.loads(path.read_text())
            record["heartbeat_at"] = time.time() - seconds
            path.write_text(json.dumps(record))


class TestAQuietSessionKeepsEnforcing(DisarmCase):
    """The worst one. Think for fifteen minutes, come back to an unguarded session."""

    def test_a_sibling_does_not_reap_a_thinking_session(self):
        self.start("victim")
        self.assertTrue(self.secret_write_denied("victim"))

        self.backdate(960)
        self.start("sibling")

        names = [p.name for p in self.session_files()]
        self.assertIn("victim.json", names, "a live session was deleted for being quiet")
        self.assertTrue(
            self.secret_write_denied("victim"),
            "a quiet session stopped refusing credential writes",
        )

    def test_deleting_the_record_does_not_disable_the_gate(self):
        """Enforcement you can remove with `rm` is not enforcement."""
        self.start("victim")
        for path in self.session_files():
            path.unlink()
        self.assertTrue(self.secret_write_denied("victim"))

    def test_an_unknown_session_is_still_governed(self):
        """No SessionStart is not a licence: the gate registers and carries on."""
        self.assertTrue(self.secret_write_denied("never-started"))

    def test_a_quiet_session_still_has_to_prove_completion(self):
        self.write("app.py", "x = 1\n")
        self.commit()
        self.start("victim")
        self.write("app.py", "x = 2\n")
        self.backdate(960)
        self.start("sibling")

        stop = self.run_hook(
            "evidence-gate",
            {"session_id": "victim", "hook_event_name": "Stop", "stop_hook_active": False},
        )
        self.assertEqual(stop.returncode, 2, "untested work was accepted after going quiet")


class TestEvidenceIsWitnessed(DisarmCase):
    """A file that says the tests passed is prose with angle brackets."""

    def project_with_a_real_break(self) -> None:
        self.write("bill.py", "def total(items):\n    return sum(items) * 10\n")
        self.write("tests/test_bill.py", "from bill import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n")
        self.write("pytest.ini", "[pytest]\n")
        self.commit()

    def stop(self):
        return self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
        )

    def test_a_hand_written_artifact_is_not_evidence(self):
        self.project_with_a_real_break()
        self.start()
        self.write("note.py", "# touched\n")
        self.write("junit.xml", '<testsuite name="s" tests="9" failures="0" errors="0"></testsuite>')
        self.assertEqual(self.stop().returncode, 2, "a fabricated artifact was accepted")

    def test_touching_a_stale_artifact_does_not_buy_a_pass(self):
        self.project_with_a_real_break()
        self.start()
        self.write("junit.xml", '<testsuite name="s" tests="1" failures="0" errors="0"></testsuite>')
        self.write("bill.py", "def total(items):\n    return sum(items) * 100\n")
        (self.repo / "junit.xml").touch()
        self.assertEqual(self.stop().returncode, 2, "`touch` cleared the freshness check")

    def test_an_honest_green_run_is_accepted(self):
        """The gate must not merely be strict — it has to let real work through."""
        self.write("bill.py", "def total(items):\n    return sum(items)\n")
        self.write("tests/test_bill.py", "from bill import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n")
        self.write("pytest.ini", "[pytest]\n")
        self.commit()
        self.start()
        self.write("bill.py", "def total(items):\n    return sum(items)  # same\n")
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--junitxml=junit.xml"],
            cwd=str(self.repo), capture_output=True, timeout=300,
        )
        self.assertEqual(self.stop().returncode, 0, self.stop().stderr)


class TestSkippedIsNotPassed(unittest.TestCase):
    """One `skipif` on a missing DATABASE_URL used to read as a full green suite."""

    def parse(self, xml: str):
        import tempfile

        from founder_os import evidence

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "junit.xml"
            path.write_text(xml)
            return evidence.parse_artifact(path)

    def test_an_all_skipped_suite_is_not_a_pass(self):
        artifact = self.parse('<testsuite name="s" tests="2" failures="0" errors="0" skipped="2"/>')
        self.assertFalse(artifact.passed)
        self.assertIn("no tests executed", artifact.detail)

    def test_a_partly_skipped_suite_counts_only_what_ran(self):
        artifact = self.parse('<testsuite name="s" tests="3" failures="0" errors="0" skipped="1"/>')
        self.assertTrue(artifact.passed)
        self.assertIn("2/2 passed", artifact.detail)
        self.assertIn("1 skipped", artifact.detail)

    def test_the_pytest_json_report_agrees(self):
        import tempfile

        from founder_os import evidence

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps({"summary": {"total": 4, "skipped": 4}, "exitcode": 0}))
            self.assertFalse(evidence.parse_artifact(path).passed)


class TestOutsideARepositoryIsNotAFailure(unittest.TestCase):
    """`~/notes` is not a repository, and refusing every edit there is not caution."""

    def gate(self, name: str, event: dict) -> subprocess.CompletedProcess:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            event["cwd"] = tmp
            return subprocess.run(
                [sys.executable, str(BIN / name)],
                input=json.dumps(event), capture_output=True, text=True, cwd=tmp, timeout=120,
            )

    def test_pre_tool_allows(self):
        proc = self.gate(
            "pre-tool",
            {
                "session_id": "x", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/a.txt", "content": "hi"},
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_stop_gate_allows(self):
        proc = self.gate(
            "evidence-gate",
            {"session_id": "x", "hook_event_name": "Stop", "stop_hook_active": False},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestConfigTyposCannotBrickTheRepository(RepoCase):
    """A hand-edited file is edited by hand, so it will contain "false" and "2000"."""

    def write_config(self, payload: dict) -> None:
        path = self.repo / ".claude" / "founder-os" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def test_a_quoted_number_does_not_block_every_tool_call(self):
        self.write_config({"max_tool_calls": "2000"})
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "a.py"), "content": "x = 1\n"},
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_string_false_means_false(self):
        from founder_os import config

        self.write_config({"leases_enabled": "false"})
        self.assertIs(config.load(self.ctx()).leases_enabled, False)

    def test_a_bare_string_is_not_iterated_as_characters(self):
        from founder_os import config

        self.write_config({"exempt_paths": "docs/"})
        self.assertEqual(config.load(self.ctx()).exempt_paths, ["docs/"])

    def test_a_bad_value_is_reported_rather_than_swallowed(self):
        from founder_os import config

        self.write_config({"lease_ttl_seconds": True, "stage_override": "banana"})
        cfg, complaints = config.load_checked(self.ctx())
        self.assertEqual(cfg.lease_ttl_seconds, 1800.0)
        self.assertIsNone(cfg.stage_override)
        self.assertEqual(len(complaints), 2, complaints)


class TestAMissingRunnerDoesNotWedgeTheSession(RepoCase):
    """The escalation counter has to advance even when the failure is an exception."""

    def test_four_strikes_still_releases(self):
        self.write(".github/workflows/ci.yml", "on: push\n")
        self.write("Dockerfile", "FROM scratch\n")
        self.write("app.py", "x = 1\n")
        self.commit()
        path = self.repo / ".claude" / "founder-os" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"test_command": ["definitely-not-installed", "test"]}))

        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.write("app.py", "x = 2\n")
        # A PASSING artifact is what makes this the wedge rather than an ordinary block:
        # the gate has to get past the artifact check and into the clean re-run, which is
        # where the missing binary used to raise straight past the escalation counter.
        self.write("junit.xml", '<testsuite name="s" tests="2" failures="0" errors="0"/>')

        codes = []
        for _ in range(6):
            codes.append(
                self.run_hook(
                    "evidence-gate",
                    {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
                ).returncode
            )
        self.assertIn(0, codes, f"never escaped after six attempts: {codes}")


class TestConcurrentIdAllocation(RepoCase):
    def test_parallel_adds_never_collide(self):
        """Same id twice merges cleanly and then `claim` acts on the wrong task."""
        workers = [
            subprocess.Popen(
                [sys.executable, str(BIN / "founder-os-plan"), "add", f"task number {i}"],
                cwd=str(self.repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for i in range(10)
        ]
        for worker in workers:
            worker.wait(timeout=180)

        files = list((self.repo / ".claude" / "founder-os" / "plan" / "next").glob("*.md"))
        ids = [f.name.split("-", 1)[0] for f in files]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10, f"duplicate ids: {sorted(ids)}")


if __name__ == "__main__":
    unittest.main()
