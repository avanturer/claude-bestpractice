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

# Stdlib unittest, never pytest: this project forbids dependencies and its CI installs
# none, so a fixture reaching for a third-party runner is red on every push. Discovery
# starts at the repo root because a bare `tests/` directory is not an importable package.
STDLIB_DISCOVER = ["-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"]


def stdlib_test(module: str, call: str, expected: str) -> str:
    """A one-assertion unittest file, so fixtures do not each hand-roll one."""
    return (
        "import unittest\n\n"
        f"from {module} import {call.split('(')[0]}\n\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_it(self):\n"
        f"        self.assertEqual({call}, {expected})\n"
    )


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
        directory = self.repo / ".git" / "claude-bestpractice" / "sessions"
        return sorted(directory.glob("*.json")) if directory.is_dir() else []

    def stdlib_runner(self) -> None:
        """Pin the test command to unittest so no fixture needs a third-party runner."""
        path = self.repo / ".claude" / "claude-bestpractice" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"test_command": [sys.executable, *STDLIB_DISCOVER]})
        )

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
        # Identity is (harness id, worktree), so the record carries a worktree tag;
        # asserting the bare filename would be asserting the key that let four
        # concurrent sessions collapse into one record.
        self.assertTrue(any(n.startswith("victim") for n in names),
                        "a live session was deleted for being quiet")
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
        self.write(
            "test_bill.py", stdlib_test("bill", "total([1, 2])", "3")
        )
        self.stdlib_runner()
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
        """The gate must not merely be strict — it has to let real work through.

        Stdlib unittest, not pytest: this project forbids dependencies and its CI
        installs none, so a fixture that reaches for pytest is red on every push. The
        suite testing a stdlib-only tool has to be stdlib-only itself.
        """
        self.write("bill.py", "def total(items):\n    return sum(items)\n")
        self.write(
            "test_bill.py", stdlib_test("bill", "total([1, 2])", "3")
        )
        self.stdlib_runner()
        self.commit()
        self.start()
        self.write("bill.py", "def total(items):\n    return sum(items)  # same\n")
        self.assertEqual(self.stop().returncode, 0, self.stop().stderr)


class TestSkippedIsNotPassed(unittest.TestCase):
    """One `skipif` on a missing DATABASE_URL used to read as a full green suite."""

    def parse(self, xml: str):
        import tempfile

        from claude_bestpractice import evidence

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

        from claude_bestpractice import evidence

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
        path = self.repo / ".claude" / "claude-bestpractice" / "config.json"
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
        from claude_bestpractice import config

        self.write_config({"leases_enabled": "false"})
        self.assertIs(config.load(self.ctx()).leases_enabled, False)

    def test_a_bare_string_is_not_iterated_as_characters(self):
        from claude_bestpractice import config

        self.write_config({"exempt_paths": "docs/"})
        self.assertEqual(config.load(self.ctx()).exempt_paths, ["docs/"])

    def test_a_bad_value_is_reported_rather_than_swallowed(self):
        from claude_bestpractice import config

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
        path = self.repo / ".claude" / "claude-bestpractice" / "config.json"
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
                [sys.executable, str(BIN / "claude-bp-plan"), "add", f"task number {i}"],
                cwd=str(self.repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for i in range(10)
        ]
        for worker in workers:
            worker.wait(timeout=180)

        files = list((self.repo / ".claude" / "claude-bestpractice" / "plan" / "next").glob("*.md"))
        ids = [f.name.split("-", 1)[0] for f in files]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10, f"duplicate ids: {sorted(ids)}")


class TestNothingCanWedgeTheGate(RepoCase):
    """Failing closed is right. Failing closed with a frozen counter is a wedge."""

    def stop(self) -> int:
        return self.run_hook(
            "evidence-gate",
            {"session_id": "w", "hook_event_name": "Stop", "stop_hook_active": False},
        ).returncode

    def test_a_config_typo_still_escalates(self):
        self.write("a.py", "x = 1\n")
        self.commit()
        self.run_hook("session-start", {"session_id": "w", "hook_event_name": "SessionStart"})
        self.write("a.py", "x = 2\n")
        for broken in ({"artifact_globs": ["/tmp/junit.xml"]}, {"artifact_globs": [""]}):
            with self.subTest(config=broken):
                path = self.repo / ".claude" / "claude-bestpractice" / "config.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(broken))
                codes = [self.stop() for _ in range(6)]
                self.assertIn(0, codes, f"a config typo wedged every Stop forever: {codes}")


class TestAStaleArtifactCannotSpeakForThisRun(DisarmCase):
    """The run is the evidence; a file is at most its detail, and only if this run wrote it."""

    def green_project(self) -> None:
        self.write("impl.py", "def f():\n    return 2\n")
        self.write("test_impl.py", stdlib_test("impl", "f()", "2"))
        self.stdlib_runner()
        self.commit()

    def stop(self) -> subprocess.CompletedProcess:
        return self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
        )

    def test_a_foreign_failing_artifact_does_not_block_a_green_run(self):
        self.green_project()
        self.start()
        self.write("impl.py", "def f():\n    return 2  # touched\n")
        self.write(
            "junit.xml",
            '<testsuite name="com.othercorp.Legacy" tests="44" failures="7" errors="0"/>',
        )
        self.assertEqual(self.stop().returncode, 0, "a stranger's artifact blocked an honest pass")

    def test_a_foreign_artifact_does_not_satisfy_the_executed_check(self):
        """A four-line file from another project used to prove tests ran here."""
        self.write("impl.py", "def f():\n    raise NotImplementedError\n")
        self.write(
            "test_impl.py",
            "import os\nimport unittest\n\n\n"
            "@unittest.skipUnless(os.environ.get('DATABASE_URL'), 'no db')\n"
            "class T(unittest.TestCase):\n    def test_it(self):\n        pass\n",
        )
        self.stdlib_runner()
        self.commit()
        self.start()
        self.write("impl.py", "def f():\n    raise NotImplementedError  # edited\n")
        self.write("junit.xml", '<testsuite name="elsewhere" tests="42" failures="0" errors="0"/>')
        self.assertEqual(self.stop().returncode, 2, "a foreign artifact stood in for a real run")


class TestBashIsAWriteTool(DisarmCase):
    """Agents write files with heredocs and redirects, not only with the Write tool."""

    def bash(self, command: str, session_id: str = "s1") -> bool:
        proc = self.run_hook(
            "pre-tool",
            {
                "session_id": session_id,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            },
        )
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        except (json.JSONDecodeError, KeyError, TypeError):
            return False

    def test_a_credential_written_by_shell_is_refused(self):
        self.start()
        for command in (
            f'cat > .env <<EOF\n{SECRET}\nEOF',
            f'echo \'{SECRET}\' >> conf.py',
            f'tee k.py <<< \'{SECRET}\'',
        ):
            with self.subTest(command=command[:24]):
                self.assertTrue(self.bash(command), "a shell write carried a credential through")

    def test_a_shell_write_respects_another_session_s_lease(self):
        self.write("app.py", "x = 1\n")
        self.commit()
        self.start("s1")
        self.run_hook(
            "pre-tool",
            {
                "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "app.py"), "content": "x = 2\n"},
            },
        )
        self.start("s2")
        for command in ("sed -i 's/x/y/' app.py", "cat > app.py <<EOF\nclobbered\nEOF", "echo hi > app.py"):
            with self.subTest(command=command[:20]):
                self.assertTrue(self.bash(command, "s2"), "a shell write silently overwrote a held file")


class TestNoCachedVerdictOutlivesItsCause(RepoCase):
    """The result cache was the richest source of defects in the gate. There is none."""

    def project(self, expected: int) -> None:
        self.write("impl.py", f"def f():\n    return {expected}\n")
        self.write(
            "test_impl.py", stdlib_test("impl", "f()", "2")
        )
        self.commit()

    def config(self, command: list) -> None:
        path = self.repo / ".claude" / "claude-bestpractice" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"test_command": command}))

    def stop(self) -> int:
        return self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
        ).returncode

    def test_a_permissive_command_does_not_certify_the_tree_forever(self):
        self.project(expected=1)
        self.config(["true"])
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.write("impl.py", "def f():\n    return 1  # edited\n")
        self.assertEqual(self.stop(), 0, "a permissive command should pass on its own terms")

        self.config([sys.executable, *STDLIB_DISCOVER])
        self.assertEqual(self.stop(), 2, "the earlier permissive pass survived the command change")

    def test_a_failure_clears_once_the_code_is_fixed(self):
        self.project(expected=2)
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.write("impl.py", "def f():\n    return 99\n")
        self.assertEqual(self.stop(), 2)
        self.write("impl.py", "def f():\n    return 2\n")
        self.assertEqual(self.stop(), 0, "a stale failure outlived the fix")


class TestTheGateCannotReEnterItself(RepoCase):
    """The gate now runs project code, so the project can point that code back at it."""

    def test_a_test_command_that_fires_the_stop_gate_terminates(self):
        script = self.repo / "selftest.sh"
        script.write_text(
            "#!/bin/sh\n"
            f'echo \'{{"cwd":"{self.repo}","hook_event_name":"Stop",'
            '"session_id":"r1","stop_hook_active":false}\' | '
            f'{sys.executable} {BIN / "evidence-gate"}\n'
            "exit 0\n"
        )
        script.chmod(0o755)
        config_path = self.repo / ".claude" / "claude-bestpractice" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"test_command": ["./selftest.sh"]}))
        self.write("app.py", "x = 1\n")
        self.commit()

        self.run_hook("session-start", {"session_id": "r1", "hook_event_name": "SessionStart"})
        self.write("app.py", "x = 2\n")
        proc = self.run_hook(
            "evidence-gate",
            {"session_id": "r1", "hook_event_name": "Stop", "stop_hook_active": False},
        )
        self.assertIn(proc.returncode, (0, 2), "the gate recursed instead of returning")


class TestARenameActuallyFailsValidation(RepoCase):
    """The README's headline memory claim, which a substring test did not deliver."""

    def test_a_rename_that_keeps_the_old_name_as_a_substring_is_caught(self):
        from claude_bestpractice import knowledge

        self.write("models.py", "class PurchaseOrder:\n    pass\n")
        self.assertFalse(
            knowledge.anchor_resolves(self.ctx(), "Order @ models.py"),
            "`Order` resolved against its own replacement `PurchaseOrder`",
        )

    def test_the_symbol_still_being_there_resolves(self):
        from claude_bestpractice import knowledge

        self.write("models.py", "class Order:\n    pass\n")
        self.assertTrue(knowledge.anchor_resolves(self.ctx(), "Order @ models.py"))


class TestTheDecisionIndexKeepsTheNewest(RepoCase):
    def test_truncation_drops_the_oldest_not_the_newest(self):
        from claude_bestpractice import knowledge

        for i in range(1, 26):
            self.write(
                f".claude/rules/decisions/{i:04d}-decision-{i}.md",
                f"---\ntitle: Decision {i}\n---\n\n## Decision\nx\n",
            )
        index = knowledge.build_index(self.ctx())
        self.assertIn("[0025]", index, "the newest decision was dropped")
        self.assertNotIn("[0001]", index, "the oldest was kept over the newest")

        claimed = int(index.split("... ")[1].split(" older")[0])
        listed = index.count("— `decisions/")
        self.assertEqual(claimed + listed, 25, "the dropped count does not add up")


class TestTheReaperReachesOtherWorktrees(RepoCase):
    def test_a_dead_sibling_s_claim_returns_to_the_queue(self):
        """The reaper runs in a surviving worktree; the dead session's file is not there."""
        from claude_bestpractice import plan
        from claude_bestpractice.gitctx import resolve

        ctx = self.ctx()
        task = plan.add(ctx, "ship the thing")
        plan.claim(ctx, task.id, "ghost", "feat/x")

        other = self.repo.parent / "sibling"
        subprocess.run(
            ["git", "worktree", "add", "--detach", "--quiet", str(other)],
            cwd=str(self.repo), capture_output=True, timeout=120, check=True,
        )
        released = plan.release(resolve(other), "ghost")
        self.assertEqual(released, 1, "a dead sibling's task stayed in flight forever")
        self.assertEqual(plan.find(ctx, task.id).state, plan.NEXT)


class TestTheQuarantineBackupIsNotWorldReadable(RepoCase):
    def test_a_backup_of_local_settings_keeps_its_secrets_private(self):
        from claude_bestpractice import conflicts

        self.write(
            ".claude/settings.local.json",
            json.dumps(
                {
                    "env": {"MY_API_TOKEN": "sk-live-not-a-real-secret"},
                    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
                }
            ),
        )
        moved, backups = conflicts.quarantine_loose_hooks(self.ctx())
        self.assertTrue(moved and backups)
        backup = self.repo / ".claude" / backups[0]
        self.assertEqual(backup.stat().st_mode & 0o077, 0, "the backup is readable by others")


class TestCLIsOutsideARepositorySaySoPlainly(unittest.TestCase):
    """A traceback reads as a crash in the tool; this is an ordinary situation."""

    CLIS = {
        "claude-bp": ["status"],
        "claude-bp-plan": ["list"],
        "claude-bp-knowledge": ["validate"],
        "claude-bp-reindex": [],
        "claude-bp-decide": ["list"],
    }

    def run_cli(self, name: str, args: list) -> subprocess.CompletedProcess:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run(
                [sys.executable, str(BIN / name), *args],
                capture_output=True, text=True, cwd=tmp, timeout=120,
            )

    def test_no_traceback(self):
        for name, args in self.CLIS.items():
            with self.subTest(cli=name):
                proc = self.run_cli(name, args)
                self.assertNotIn("Traceback", proc.stderr, f"{name} dumped a traceback")
                self.assertIn("git repository", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
