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

CLI = BIN / "claude-bp-ci"


class CICase(RepoCase):
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )

    def ctx(self):
        from claude_bestpractice.gitctx import resolve

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
        from claude_bestpractice import ci

        first, _ = ci.install(self.ctx())
        second, note = ci.install(self.ctx())
        self.assertTrue(first)
        self.assertFalse(second, "a second install rewrote the hook")
        self.assertIn("already", note)

    def test_it_never_silently_destroys_another_tool_s_hook(self):
        from claude_bestpractice import ci

        path = ci.hook_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\necho someone-elses-hook\n")

        _, note = ci.install(self.ctx())
        displaced = path.parent / ci.DISPLACED_NAME
        self.assertTrue(displaced.exists(), "the previous hook was destroyed")
        self.assertIn("someone-elses-hook", displaced.read_text())
        self.assertIn("runs FIRST", note, "the founder was not told their hook moved")

        ci.remove(self.ctx())
        self.assertIn("someone-elses-hook", path.read_text(), "the previous hook was not restored")

    def test_the_displaced_hook_still_runs(self):
        """Moving a hook aside without running it switches off a check, silently.

        That is the exact failure this project exists to prevent, committed by the thing
        that prevents it — and it would have shipped as "backed up", which sounds safe.
        """
        from claude_bestpractice import ci

        path = ci.hook_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\necho THEIR-CHECK-RAN\nexit 0\n")
        path.chmod(0o755)
        ci.install(self.ctx())

        proc = subprocess.run(
            ["sh", str(path)], cwd=str(self.repo), capture_output=True, text=True, timeout=60,
        )
        self.assertIn("THEIR-CHECK-RAN", proc.stdout)

    def test_a_refusal_from_the_displaced_hook_is_still_a_refusal(self):
        from claude_bestpractice import ci

        path = ci.hook_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\necho nope >&2\nexit 3\n")
        path.chmod(0o755)
        ci.install(self.ctx())

        proc = subprocess.run(
            ["sh", str(path)], cwd=str(self.repo), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 3, "the displaced hook's refusal was swallowed")

    def test_it_does_not_write_through_a_symlink(self):
        """husky and lefthook both symlink the hook at a script in the working tree.

        `Path.exists()` follows symlinks and `write_text` writes through them, so the
        hook body landed inside the founder's own tracked source file. `git status`
        showed their script modified, and the undo could not put it back because it
        restored a hook, not the file.
        """
        from claude_bestpractice import ci

        target = self.repo / "scripts" / "prepush.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\necho MY-OWN-CHECK\n")
        target.chmod(0o755)

        path = ci.hook_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

        ci.install(self.ctx())
        self.assertEqual(
            target.read_text(), "#!/bin/sh\necho MY-OWN-CHECK\n",
            "the hook body was written through the symlink into tracked source",
        )

        ci.remove(self.ctx())
        self.assertTrue(path.is_symlink(), "the symlink was not put back as a symlink")
        self.assertEqual(path.resolve(), target.resolve())

    def test_removal_reports_that_nothing_checks_pushes(self):
        from claude_bestpractice import ci

        ci.install(self.ctx())
        changed, note = ci.remove(self.ctx())
        self.assertTrue(changed)
        self.assertFalse(ci.installed(self.ctx()))
        self.assertIn("Nothing checks", note)

    def test_it_honours_core_hookspath(self):
        """A repo that configured its own hooks directory gets the hook there, or nowhere."""
        from claude_bestpractice import ci

        (self.repo / "githooks").mkdir()
        git(["config", "core.hooksPath", "githooks"], self.repo)
        ci.install(self.ctx())
        self.assertTrue((self.repo / "githooks" / ci.HOOK_NAME).exists())
        self.assertTrue(ci.installed(self.ctx()))

    def test_the_hook_is_executable(self):
        """git silently ignores a hook without the bit, which looks exactly like passing."""
        from claude_bestpractice import ci

        ci.install(self.ctx())
        self.assertTrue(ci.hook_path(self.ctx()).stat().st_mode & 0o111)


class TestLocalIsTheDefault(CICase):
    """An opt-in check nobody opted into is the same as no check."""

    def test_setup_installs_it(self):
        from claude_bestpractice import ci

        self.run_hook("setup", {"session_id": "s1", "hook_event_name": "Setup"})
        self.assertTrue(ci.installed(self.ctx()), "setup left pushes unchecked")

    def test_init_installs_it(self):
        from claude_bestpractice import ci

        subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), "init"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertTrue(ci.installed(self.ctx()))

    def test_status_says_what_runs_where(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), "status"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertIn("CHECKS", proc.stdout)
        self.assertIn("pre-push", proc.stdout)


class TestHostedCICostsNothingUntilAskedFor(CICase):
    def workflow(self, gated: bool) -> None:
        path = self.repo / ".github" / "workflows" / "check.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        guard = "    if: vars.CLAUDE_BESTPRACTICE_CI == 'on'\n" if gated else ""
        path.write_text(f"name: check\non:\n  push:\njobs:\n  check:\n{guard}    runs-on: ubuntu-latest\n")

    def test_a_gated_workflow_is_reported_as_opt_in(self):
        from claude_bestpractice import ci

        self.workflow(gated=True)
        self.assertEqual(ci.workflow_state(self.ctx()), "gated")
        self.assertTrue(any("gated" in line for line in ci.status_lines(self.ctx())))

    def test_an_ungated_workflow_is_called_out_as_spending_minutes(self):
        from claude_bestpractice import ci

        self.workflow(gated=False)
        self.assertEqual(ci.workflow_state(self.ctx()), "always")
        self.assertTrue(any("UNGATED" in line for line in ci.status_lines(self.ctx())))

    def test_enabling_without_gh_explains_the_manual_route(self):
        from claude_bestpractice import ci

        self.workflow(gated=True)
        ok, note = ci.set_hosted(self.ctx(), on=True)
        # gh may or may not exist on the machine running these tests; both paths must be
        # honest rather than silently claiming success.
        if ok:
            self.assertIn("hosted CI is now on", note)
        else:
            self.assertTrue("gh variable set" in note or "gh refused" in note, note)

    def test_it_refuses_when_there_is_no_workflow(self):
        from claude_bestpractice import ci

        ok, note = ci.set_hosted(self.ctx(), on=True)
        self.assertFalse(ok)
        self.assertIn("no .github/workflows", note)


class TestTheShippedWorkflowIsOptIn(unittest.TestCase):
    """This repository's own CI must not spend minutes on every push either."""

    def test_the_check_job_is_gated(self):
        from claude_bestpractice import ci

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


class TestTheHookRunsTheProjectsOwnSuite(CICase):
    """`make check` or the doctor was the whole ladder, and the doctor tests the PLUGIN.

    A Node or Go project with no Makefile therefore had its push "checked" by a run that
    never touched a line of its code, while the founder reasonably believed a pre-push
    gate was guarding it. `detect_test_command` already knew the right answer and nothing
    asked it.
    """

    def test_a_node_project_gets_npm_test(self):
        from claude_bestpractice import ci

        self.write("package.json", '{"name":"x","scripts":{"test":"vitest run"}}')
        ci.install(self.ctx())
        body = ci.hook_path(self.ctx()).read_text()
        self.assertIn("npm", body)
        self.assertIn("_runner=npm", body, "the tier must not fail when npm is absent")

    def test_a_vanished_runner_refuses_the_push(self):
        """The one fail-open path left in the ladder, and the worst one.

        A runner detected at install time and missing at push time used to fall past
        this tier, past `claude-bp-ci` (not on a marketplace user's shell PATH), past
        `claude-bp-doctor` (same), and out through `exit 0` — so a project that HAS a
        suite pushed with nothing run, reported as checked. Refuse instead: a gate that
        cannot verify must not pretend it did.
        """
        import os
        import shutil

        from claude_bestpractice import ci

        self.write("package.json", '{"name":"x","scripts":{"test":"vitest run"}}')
        ci.install(self.ctx())

        # A PATH with just enough to reach the tier — and no npm.
        stub = self.repo.parent / "stubbin"
        stub.mkdir(exist_ok=True)
        for tool in ("sh", "dirname", "grep"):
            found = shutil.which(tool)
            self.assertIsNotNone(found, f"the fixture needs {tool}")
            (stub / tool).symlink_to(found)

        proc = subprocess.run(
            ["sh", str(ci.hook_path(self.ctx()))],
            cwd=str(self.repo), capture_output=True, text=True, timeout=60,
            env={**os.environ, "PATH": str(stub)},
        )
        self.assertEqual(proc.returncode, 1, f"the push was allowed: {proc.stdout}{proc.stderr}")
        self.assertIn("never happened", proc.stderr)
        self.assertIn("--no-verify", proc.stderr, "refusing without naming the escape hatch")

    def test_a_project_with_no_runner_still_allows_the_push(self):
        """The other side of the same coin: nothing to run is a true statement.

        Refusing every push in a repository that has no suite yet would get the hook
        deleted within a day, and rightly — there is no check being skipped.
        """
        from claude_bestpractice import ci

        ci.install(self.ctx())
        body = ci.hook_path(self.ctx()).read_text()
        self.assertNotIn("Refusing the push", body)
        self.assertIn("exit 0", body)

    def test_the_doctor_is_still_the_last_resort(self):
        """A repository with no runner at all still gets its gates proven."""
        from claude_bestpractice import ci

        ci.install(self.ctx())
        body = ci.hook_path(self.ctx()).read_text()
        self.assertIn("claude-bp-doctor", body)

    def test_an_undetectable_runner_leaves_a_valid_script(self):
        from claude_bestpractice import ci

        ci.install(self.ctx())
        body = ci.hook_path(self.ctx()).read_text()
        self.assertNotIn("__TEST_COMMAND__", body, "the placeholder shipped unrendered")
        proc = subprocess.run(["sh", "-n", str(ci.hook_path(self.ctx()))],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_command_is_quoted(self):
        """A path with a space in it must not split into two arguments."""
        from claude_bestpractice import ci

        self.assertNotIn("__TEST_COMMAND__", ci.hook_body(self.ctx()))


class TestTheGateArmsItself(CICase):
    """`✓ enabled` and no hook guarding any push was the default install.

    `Setup` fires on `--init`, so the hook reached repositories that were created through
    the plugin and no others. Every founder who did the documented thing — install into
    the repository they already had — got gates that fire in-session and a push path with
    nothing on it at all.
    """

    def test_a_session_start_arms_an_unguarded_repository(self):
        from claude_bestpractice import ci

        self.assertFalse(ci.installed(self.ctx()))
        armed, _ = ci.ensure(self.ctx())
        self.assertTrue(armed)
        self.assertTrue(ci.installed(self.ctx()))

    def test_arming_twice_changes_nothing(self):
        """Eight sessions start at once; seven of them must be no-ops."""
        from claude_bestpractice import ci

        ci.ensure(self.ctx())
        before = ci.hook_path(self.ctx()).read_text()
        armed, _ = ci.ensure(self.ctx())
        self.assertFalse(armed)
        self.assertEqual(before, ci.hook_path(self.ctx()).read_text())

    def test_off_stays_off(self):
        """A tool that re-arms what its owner just switched off is arguing with them."""
        from claude_bestpractice import ci

        ci.ensure(self.ctx())
        ci.remove(self.ctx())
        self.assertTrue(ci.declined(self.ctx()))

        armed, _ = ci.ensure(self.ctx())
        self.assertFalse(armed, "the opt-out was overridden by the next session start")
        self.assertFalse(ci.installed(self.ctx()))

    def test_asking_for_it_back_works(self):
        """Otherwise `ci on` appears to work and the next session start undoes it."""
        from claude_bestpractice import ci

        ci.remove(self.ctx())
        ci.install(self.ctx())
        self.assertFalse(ci.declined(self.ctx()))

        ci.remove(self.ctx())
        ci.install(self.ctx())
        armed, _ = ci.ensure(self.ctx())
        self.assertFalse(armed)
        self.assertTrue(ci.installed(self.ctx()), "the hook did not survive")

    def test_the_real_session_start_gate_arms_it(self):
        """The unit above proves `ensure`; this proves the gate actually calls it.

        Verified against the shipped plugin before this existed: install into an
        existing repository, start a session, and `.git/hooks/pre-push` was still absent.
        """
        from claude_bestpractice import ci

        proc = self.run_hook(
            "session-start", {"session_id": "s1", "hook_event_name": "SessionStart"}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(ci.installed(self.ctx()), "a session started and left the push path open")
        self.assertIn("before every push", proc.stdout)

    def test_the_session_start_gate_honours_the_optout(self):
        from claude_bestpractice import ci

        ci.remove(self.ctx())
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.assertFalse(ci.installed(self.ctx()))

    def test_the_second_session_says_nothing(self):
        """The always-on context budget is 400 tokens; this line is not always-on."""
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        second = self.run_hook(
            "session-start", {"session_id": "s2", "hook_event_name": "SessionStart"}
        )
        self.assertNotIn("before every push", second.stdout)

    def test_the_optout_is_not_committed(self):
        """One machine's opt-out must not travel to every checkout of the branch."""
        from claude_bestpractice import ci

        ci.remove(self.ctx())
        self.assertEqual("", git(["status", "--porcelain"], self.repo).strip())


if __name__ == "__main__":
    unittest.main()
