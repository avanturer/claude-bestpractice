"""Where the checks run — the local pre-push gate and the hosted-CI switch.

The property that matters is not "a file was written". It is that a red check actually
stops a push, that turning it off restores whatever was there before, and that the
hosted workflow costs nothing until it is switched on. So most of this drives real
`git push` against a real bare remote.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import BIN, REPO_ROOT, RepoCase, git

CLI = BIN / "founder-os-ci"


class CICase(RepoCase):
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )

    def ctx(self):
        from founder_os.gitctx import resolve

        return resolve(self.repo)

    def with_remote(self) -> Path:
        bare = self.repo.parent / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, timeout=60)
        git(["remote", "add", "origin", str(bare)], self.repo)
        return bare

    def make_check(self, exit_code: int) -> None:
        body = "check:\n\t@echo 'ran'\n" if exit_code == 0 else "check:\n\t@echo 'red'; exit 1\n"
        (self.repo / "Makefile").write_text(body)
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "makefile"], self.repo)


class TestTheHookStopsAPush(CICase):
    """A hook that runs but cannot refuse is decoration."""

    def test_a_red_check_blocks_the_push(self):
        self.with_remote()
        self.make_check(exit_code=1)
        self.cli("local")
        proc = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertNotEqual(proc.returncode, 0, "a failing check let the push through")
        self.assertIn("red", proc.stdout + proc.stderr)

    def test_a_green_check_lets_the_push_through(self):
        self.with_remote()
        self.make_check(exit_code=0)
        self.cli("local")
        proc = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_verify_is_the_deliberate_bypass(self):
        """Escapable on purpose — an inescapable gate is one that gets uninstalled."""
        self.with_remote()
        self.make_check(exit_code=1)
        self.cli("local")
        proc = subprocess.run(
            ["git", "push", "--no-verify", "origin", "main"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestInstallIsSafe(CICase):
    def test_it_is_idempotent(self):
        from founder_os import ci

        first, _ = ci.install(self.ctx())
        second, note = ci.install(self.ctx())
        self.assertTrue(first)
        self.assertFalse(second, "a second install rewrote the hook")
        self.assertIn("already", note)

    def test_it_never_silently_destroys_another_tool_s_hook(self):
        from founder_os import ci

        path = ci.hook_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\necho someone-elses-hook\n")

        ci.install(self.ctx())
        backup = path.with_suffix(path.suffix + ci.BACKUP_SUFFIX)
        self.assertTrue(backup.exists(), "the previous hook was destroyed")
        self.assertIn("someone-elses-hook", backup.read_text())

        ci.remove(self.ctx())
        self.assertIn("someone-elses-hook", path.read_text(), "the previous hook was not restored")

    def test_removal_reports_that_nothing_checks_pushes(self):
        from founder_os import ci

        ci.install(self.ctx())
        changed, note = ci.remove(self.ctx())
        self.assertTrue(changed)
        self.assertFalse(ci.installed(self.ctx()))
        self.assertIn("Nothing checks", note)

    def test_it_honours_core_hookspath(self):
        """A repo that configured its own hooks directory gets the hook there, or nowhere."""
        from founder_os import ci

        (self.repo / "githooks").mkdir()
        git(["config", "core.hooksPath", "githooks"], self.repo)
        ci.install(self.ctx())
        self.assertTrue((self.repo / "githooks" / ci.HOOK_NAME).exists())
        self.assertTrue(ci.installed(self.ctx()))

    def test_the_hook_is_executable(self):
        """git silently ignores a hook without the bit, which looks exactly like passing."""
        from founder_os import ci

        ci.install(self.ctx())
        self.assertTrue(ci.hook_path(self.ctx()).stat().st_mode & 0o111)


class TestLocalIsTheDefault(CICase):
    """An opt-in check nobody opted into is the same as no check."""

    def test_setup_installs_it(self):
        from founder_os import ci

        self.run_hook("setup", {"session_id": "s1", "hook_event_name": "Setup"})
        self.assertTrue(ci.installed(self.ctx()), "setup left pushes unchecked")

    def test_init_installs_it(self):
        from founder_os import ci

        subprocess.run(
            [sys.executable, str(BIN / "founder-os"), "init"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertTrue(ci.installed(self.ctx()))

    def test_status_says_what_runs_where(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "founder-os"), "status"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertIn("CHECKS", proc.stdout)
        self.assertIn("pre-push", proc.stdout)


class TestHostedCICostsNothingUntilAskedFor(CICase):
    def workflow(self, gated: bool) -> None:
        path = self.repo / ".github" / "workflows" / "check.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        guard = "    if: vars.FOUNDER_OS_CI == 'on'\n" if gated else ""
        path.write_text(f"name: check\non:\n  push:\njobs:\n  check:\n{guard}    runs-on: ubuntu-latest\n")

    def test_a_gated_workflow_is_reported_as_opt_in(self):
        from founder_os import ci

        self.workflow(gated=True)
        self.assertEqual(ci.workflow_state(self.ctx()), "gated")
        self.assertTrue(any("gated" in line for line in ci.status_lines(self.ctx())))

    def test_an_ungated_workflow_is_called_out_as_spending_minutes(self):
        from founder_os import ci

        self.workflow(gated=False)
        self.assertEqual(ci.workflow_state(self.ctx()), "always")
        self.assertTrue(any("UNGATED" in line for line in ci.status_lines(self.ctx())))

    def test_enabling_without_gh_explains_the_manual_route(self):
        from founder_os import ci

        self.workflow(gated=True)
        ok, note = ci.set_hosted(self.ctx(), on=True)
        # gh may or may not exist on the machine running these tests; both paths must be
        # honest rather than silently claiming success.
        if ok:
            self.assertIn("hosted CI is now on", note)
        else:
            self.assertTrue("gh variable set" in note or "gh refused" in note, note)

    def test_it_refuses_when_there_is_no_workflow(self):
        from founder_os import ci

        ok, note = ci.set_hosted(self.ctx(), on=True)
        self.assertFalse(ok)
        self.assertIn("no .github/workflows", note)


class TestTheShippedWorkflowIsOptIn(unittest.TestCase):
    """This repository's own CI must not spend minutes on every push either."""

    def test_the_check_job_is_gated(self):
        from founder_os import ci

        text = (REPO_ROOT / ci.WORKFLOW).read_text(encoding="utf-8")
        self.assertIn(f"vars.{ci.CI_VARIABLE} == 'on'", text)

    def test_the_workflow_runs_the_same_gates_as_the_hook(self):
        """Two check surfaces that disagree is worse than one."""
        text = (REPO_ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8")
        for target in ("make lint", "make docs", "make slop", "make test", "make doctor"):
            self.assertIn(target, text, f"hosted CI does not run `{target}`")

    def test_ci_installs_no_dependencies(self):
        """The stdlib-only constraint is void if CI quietly pip-installs the difference."""
        text = (REPO_ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8")
        self.assertNotIn("pip install", text)


if __name__ == "__main__":
    unittest.main()
