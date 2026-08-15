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

    def test_ci_installs_nothing_the_plugin_could_import(self):
        """Narrowed from "installs nothing at all", and the narrowing is load-bearing.

        The original reason was "the stdlib-only constraint is void if CI quietly
        pip-installs the difference", and that is not how the constraint is enforced.
        `tools/check_stdlib_only.py` reads the source, so it refuses `import requests` in
        `plugin/` whether or not requests is installed — verified by adding one and
        watching it fail with both requests and pytest present in the environment. This
        assertion was belt to that braces, and the belt was the wrong size: it also
        forbade the one install the suite genuinely needs.

        Three tests build a throwaway Python project and require the gate to actually
        execute pytest over it. A bare runner has none, the gate correctly declines, and
        those three fail — which is what happened the first time anything ran `make check`
        on a clean machine. So exactly one install is permitted, it is named here, and the
        plugin may not import it.
        """
        import re

        text = (REPO_ROOT / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8")
        installs = re.findall(r"pip install.*", text)
        self.assertEqual(len(installs), 1, f"unexpected install step(s): {installs}")
        self.assertIn("pytest", installs[0])

        offenders = [
            path.name
            for path in (REPO_ROOT / "plugin" / "lib" / "claude_bestpractice").glob("*.py")
            if re.search(r"^\s*(import|from)\s+pytest\b", path.read_text(encoding="utf-8"), re.M)
        ]
        self.assertEqual(offenders, [], "the plugin imports what CI installs")


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


class TestTheStatusLineIsTrue(CICase):
    """`hosted CI: no workflow in this repository` was a claim about the repository.

    It was computed from a test for one file — ours. A repository with four workflows of
    its own was told it had none, one line under `stage: … CI config present`, so two
    lines of the same output contradicted each other and the wrong one sounded certain.
    """

    def test_a_repository_with_its_own_workflows_is_not_told_it_has_none(self):
        from claude_bestpractice import ci

        self.write(".github/workflows/deploy.yml", "on: push\njobs: {}\n")
        self.write(".github/workflows/lint.yaml", "on: push\njobs: {}\n")
        lines = "\n".join(ci.status_lines(self.ctx()))
        self.assertNotIn("no workflow in this repository", lines)
        self.assertIn("2 workflow(s) of your own", lines)

    def test_a_repository_with_none_is_still_told_so(self):
        from claude_bestpractice import ci

        self.assertIn("no workflow in this repository", "\n".join(ci.status_lines(self.ctx())))

    def test_our_own_workflow_is_not_counted_as_a_foreign_one(self):
        from claude_bestpractice import ci

        self.write(ci.WORKFLOW, f"on: push\n# {ci.CI_VARIABLE}\n")
        lines = "\n".join(ci.status_lines(self.ctx()))
        self.assertIn("gated on", lines)
        self.assertNotIn("of your own", lines)


class TestLookingDoesNotWrite(CICase):
    """A command named `status` was creating a file and leaving it untracked."""

    def test_status_leaves_the_working_tree_clean(self):
        import subprocess
        import sys

        from helpers import BIN

        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), "status"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            "", git(["status", "--porcelain"], self.repo).strip(),
            "`status` mutated the repository it was asked to report on",
        )

    def test_the_gates_still_record_the_stage(self):
        """The write was not wrong, only its caller. Something must still ratchet."""
        from claude_bestpractice import stage

        self.write("package.json", '{"dependencies": {"stripe": "^14.0.0"}}')
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.assertEqual(stage.recorded_stage(self.ctx()), stage.REVENUE)

    def test_a_session_start_leaves_a_prototype_clean_too(self):
        """`status` was fixed and the gates were not, so the file came back anyway.

        Reported from a real install: `git status` in a repository that had done nothing
        but start a session came back with `?? .claude/claude-bestpractice/stage/`. The
        floor marker records a stage nothing can regress below, so it was pure residue.
        """
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.assertEqual(
            "", git(["status", "--porcelain", "--untracked-files=all"], self.repo).strip(),
            "a session start dirtied the working tree",
        )


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

        self.write("tests/test_x.py", "def test_x():\n    assert True\n")
        proc = self.run_hook(
            "session-start", {"session_id": "s1", "hook_event_name": "SessionStart"}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(ci.installed(self.ctx()), "a session started and left the push path open")
        self.assertIn("before every push", proc.stdout)

    def test_the_line_names_the_command_it_is_promising(self):
        """"Checks now run" is not checkable, and the founder cannot tell which checks."""
        self.write("tests/test_x.py", "def test_x():\n    assert True\n")
        proc = self.run_hook(
            "session-start", {"session_id": "s1", "hook_event_name": "SessionStart"}
        )
        self.assertIn("pytest", proc.stdout)

    def test_a_make_check_target_is_named_ahead_of_the_runner(self):
        self.write("Makefile", "check:\n\t@echo ok\n")
        self.write("tests/test_x.py", "def test_x():\n    assert True\n")
        proc = self.run_hook(
            "session-start", {"session_id": "s1", "hook_event_name": "SessionStart"}
        )
        self.assertIn("make check", proc.stdout)
        self.assertNotIn("pytest", proc.stdout)

    def test_it_does_not_promise_a_check_that_will_not_run(self):
        """The board said "checks now run before every push" in every repository.

        Including one with no `make check` target and no detectable runner, where the
        hook reaches `claude-bp-ci` by name, does not find it on a marketplace user's
        PATH, and exits 0. A promise larger than the fact is the exact failure this
        project is written against, and it was this project making it.
        """
        proc = self.run_hook(
            "session-start", {"session_id": "s1", "hook_event_name": "SessionStart"}
        )
        self.assertNotIn("before every push", proc.stdout)
        self.assertIn("refuse nothing", proc.stdout)

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

    def age_the_hook(self) -> None:
        """Restamp the installed hook as if an older plugin had written it."""
        from claude_bestpractice import __version__, ci

        path = ci.hook_path(self.ctx())
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                f"{ci.STAMP} {__version__}", f"{ci.STAMP} 0.9.0"),
            encoding="utf-8",
        )

    def test_an_older_hook_is_updated_by_the_new_plugin(self):
        """The hook was written once and then never again, so every fix to it reached new
        repositories only. v1.0.0 shipped a serious one — an `exit 0` where a project WITH
        a suite pushed with nothing run — and anyone already using the plugin kept the
        buggy hook forever, with no way to know. Updating the plugin has to update what
        the plugin installed.
        """
        from claude_bestpractice import __version__, ci

        ci.ensure(self.ctx())
        self.age_the_hook()
        self.assertEqual(ci.stamped_version(self.ctx()), "0.9.0")

        changed, note = ci.ensure(self.ctx())
        self.assertTrue(changed)
        self.assertEqual(note, "refreshed")
        self.assertEqual(ci.stamped_version(self.ctx()), __version__)

    def test_a_current_hook_is_left_alone(self):
        """Rewriting it every session start would churn a file the founder may have read."""
        from claude_bestpractice import ci

        ci.ensure(self.ctx())
        before = ci.hook_path(self.ctx()).read_text(encoding="utf-8")
        self.assertEqual(ci.ensure(self.ctx()), (False, ""))
        self.assertEqual(ci.hook_path(self.ctx()).read_text(encoding="utf-8"), before)

    def test_refreshing_does_not_eat_the_hook_it_displaced(self):
        """`install()` moves whatever is at this path aside and chains it. Reusing that
        path on a refresh would move OUR hook onto the founder's husky script — the one
        thing this module has always refused to do."""
        from claude_bestpractice import __version__, ci

        theirs = ci.hooks_dir(self.ctx())
        theirs.mkdir(parents=True, exist_ok=True)
        (theirs / "pre-push").write_text("#!/bin/sh\necho theirs\n", encoding="utf-8")
        ci.ensure(self.ctx())

        displaced = theirs / ci.DISPLACED_NAME
        self.assertIn("echo theirs", displaced.read_text(encoding="utf-8"))

        self.age_the_hook()
        ci.ensure(self.ctx())
        self.assertIn("echo theirs", displaced.read_text(encoding="utf-8"))

    def test_an_optout_still_beats_a_refresh(self):
        from claude_bestpractice import ci

        ci.ensure(self.ctx())
        ci.remove(self.ctx())
        self.assertEqual(ci.ensure(self.ctx())[0], False)
        self.assertFalse(ci.installed(self.ctx()))

    def test_the_optout_is_not_committed(self):
        """One machine's opt-out must not travel to every checkout of the branch."""
        from claude_bestpractice import ci

        ci.remove(self.ctx())
        self.assertEqual("", git(["status", "--porcelain"], self.repo).strip())


if __name__ == "__main__":
    unittest.main()


class TestALanguageIsNotADirectoryName(CICase):
    """`python3 -m pytest -q` was baked into a Ruby project's push hook.

    The fallback asked whether a directory named `test` or `tests` existed and concluded
    Python. Jekyll, gson and guzzle each have one and none of them is Python. pytest exits
    5 for "no tests ran", so the hook refused every push out of that repository —
    permanently, over a command naming no file in it.

    Found by cloning eleven real repositories, installing into each, and pushing.
    """

    def test_a_ruby_layout_is_not_read_as_python(self):
        from claude_bestpractice.config import detect_test_command

        self.write("Gemfile", "source 'https://rubygems.org'\n")
        self.write("Rakefile", "task :test\n")
        self.write("test/helper.rb", "require 'minitest'\n")
        self.write("lib/thing.rb", "class Thing; end\n")
        self.assertEqual([], detect_test_command(self.repo))

    def test_a_java_layout_is_not_read_as_python(self):
        from claude_bestpractice.config import detect_test_command

        self.write("src/test/java/AppTest.java", "class AppTest {}\n")
        self.assertEqual([], detect_test_command(self.repo))

    def test_python_tests_are_still_found_where_they_live(self):
        """The fallback must keep working for the projects it was written for."""
        from claude_bestpractice.config import detect_test_command

        self.write("tests/test_thing.py", "def test_x():\n    assert True\n")
        self.assertEqual(["python3", "-m", "pytest", "-q"], detect_test_command(self.repo))

    def test_a_nested_python_layout_is_found_too(self):
        from claude_bestpractice.config import detect_test_command

        self.write("tests/unit/test_deep.py", "def test_x():\n    assert True\n")
        self.assertEqual(["python3", "-m", "pytest", "-q"], detect_test_command(self.repo))

    def test_a_ruby_project_can_still_push(self):
        """The consequence, end to end: the hook must not refuse what it cannot check."""
        import subprocess

        from claude_bestpractice import ci

        self.write("Gemfile", "source 'https://rubygems.org'\n")
        self.write("test/helper.rb", "require 'minitest'\n")
        self.commit()
        ci.install(self.ctx())

        # WITH the plugin on PATH, which is where a marketplace install puts it. This
        # assertion used to hold only for someone who does not use this plugin: the hook
        # fell through to a `claude-bp-doctor` tier that resolved whenever `bin/` was on
        # PATH, so "nothing to run" was never reached and CI was green for the same
        # reason the founder's machine was red. Reported as issue #30.
        import os

        env = dict(os.environ, PATH=f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}")
        proc = subprocess.run(
            ["sh", str(ci.hook_path(self.ctx()))],
            cwd=str(self.repo), capture_output=True, text=True, timeout=120, env=env,
        )
        self.assertEqual(0, proc.returncode, f"a Ruby project could not push: {proc.stderr}")
        self.assertIn("nothing to run", proc.stderr)

    def test_the_push_gate_never_runs_this_plugins_own_doctor(self):
        """Proving THIS PLUGIN's gates fire says nothing about the code being pushed.

        A doctor failure caused by the environment rejected a push of healthy code, ~40s
        of self-test ran in place of the repository's own checks, and in this repository
        it closed a loop: claude-bestpractice refused to let claude-bestpractice be pushed
        from a session.
        """
        from claude_bestpractice import ci

        # Executable lines only. The comment explaining the removal names the command, and
        # a test that cannot tell a comment from a call would forbid saying why.
        runnable = [
            line for line in ci.hook_body(self.ctx()).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertNotIn("claude-bp-doctor", "\n".join(runnable))


class TestAPrePushRunIsEvidence(RepoCase):
    """Issue #83. The plugin runs the project's checks on every push, sees the exit code,
    and threw the observation away — `record_green` was reachable only from the Stop gate,
    which writes for the branch of the tree the SESSION occupies.

    So the session that merges, which stands in the main checkout by this plugin's own
    design, was asked for evidence only a session inside the branch's worktree could ever
    create. Two thousand passing tests, run twice in the right tree and once by this very
    hook, counted for nothing.
    """

    def fires(self, makefile: str) -> tuple:
        """Install the hook against a given Makefile and run it as git would."""
        from claude_bestpractice import ci, evidence

        ctx = self.ctx()
        self.write("Makefile", makefile)
        self.commit("a project with checks of its own")
        ci.ensure(ctx)
        self.assertIsNone(evidence.last_green(ctx), "green before anything ran")
        proc = subprocess.run(
            [str(ci.hook_path(ctx))], cwd=str(self.repo),
            capture_output=True, text=True, timeout=180,
        )
        return proc, evidence.last_green(self.ctx())

    def test_every_tier_that_can_run_a_suite_records_it(self):
        """One test per TIER, not per fix.

        #84 added recording to the two literal tiers of the template and missed the one
        generated at install time — which is the tier that fires for a project with a
        detected runner and no `check:` target, i.e. most of them. It still ended in
        `exec`, so the shell was replaced and the recording line could not exist. The
        coverage written for #84 exercised the `make check` tier, which already worked
        (#87). Enumerated here so a new tier cannot be added without one.
        """
        for makefile, tier in (
            ("check:\n\t@true\n", "check target"),
            ("test:\n\t@true\n", "detected runner, no check target"),
        ):
            with self.subTest(tier=tier):
                proc, observed = self.fires(makefile)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertIsNotNone(observed, f"{tier}: the suite ran and nothing was recorded")
                self.assertEqual(self.ctx().branch, observed.get("branch"))
                (self.repo / "Makefile").unlink()
                from claude_bestpractice import ci, store

                store.tier_b(self.ctx(), "green").exists() and __import__("shutil").rmtree(
                    store.tier_b(self.ctx(), "green"))
                ci.hook_path(self.ctx()).unlink(missing_ok=True)

    def test_the_hook_records_the_run_it_just_watched_pass(self):
        from claude_bestpractice import ci, evidence

        ctx = self.ctx()
        self.write("Makefile", "check:\n\t@true\n")
        self.commit("a project with checks of its own")
        ci.ensure(ctx)
        self.assertIsNone(evidence.last_green(ctx), "green before anything ran")

        hook = ci.hook_path(ctx)
        proc = subprocess.run(
            [str(hook)], cwd=str(self.repo), capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        observed = evidence.last_green(ctx)
        self.assertIsNotNone(observed, "the hook ran the suite and recorded nothing")
        self.assertEqual(self.ctx().branch, observed.get("branch"))

    def test_a_failing_run_records_nothing(self):
        """The recorder sits after the exit-code check, not beside it."""
        from claude_bestpractice import ci, evidence

        ctx = self.ctx()
        self.write("Makefile", "check:\n\t@exit 1\n")
        self.commit("a project whose checks fail")
        ci.ensure(ctx)

        proc = subprocess.run(
            [str(ci.hook_path(ctx))], cwd=str(self.repo), capture_output=True, text=True, timeout=180,
        )
        self.assertNotEqual(0, proc.returncode)
        self.assertIsNone(evidence.last_green(ctx), "a red run was recorded as green")


class TestAStaleHookIsBroughtUpToDate(RepoCase):
    """Issue #85. `ensure()` has upgraded stale hooks since #33; `install()` predates that
    and short-circuited on existence — so `claude-bp-ci local`, the command whose whole
    purpose is running the checks locally, was the one that declined to update them.

    It matters because of what a stale hook silently does: across 1.0.x the body changed
    several times, including a release where a project WITH a suite pushed with nothing
    run. A founder running this after an upgrade believes they have the shipped gate.
    """

    def stale(self) -> None:
        from claude_bestpractice import ci

        ctx = self.ctx()
        ci.install(ctx)
        path = ci.hook_path(ctx)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                f"{ci.STAMP} ", f"{ci.STAMP} 1.0.0 #", 1),
            encoding="utf-8",
        )

    def test_it_rewrites_a_hook_an_older_plugin_wrote(self):
        from claude_bestpractice import __version__, ci

        self.stale()
        changed, note = ci.install(self.ctx())
        self.assertTrue(changed, note)
        self.assertIn("updated", note)
        self.assertEqual(__version__, ci.stamped_version(self.ctx()))

    def test_it_says_which_versions_rather_than_only_that_a_hook_exists(self):
        """"already installed" tells the founder nothing they can act on."""
        from claude_bestpractice import ci

        ci.install(self.ctx())
        changed, note = ci.install(self.ctx())
        self.assertFalse(changed)
        self.assertIn("current", note)

    def test_it_still_refuses_to_touch_a_hook_that_is_not_ours(self):
        """The one thing this module has always refused to do."""
        from claude_bestpractice import ci

        ctx = self.ctx()
        path = ci.hook_path(ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\necho husky\n", encoding="utf-8")

        ci.install(ctx)
        displaced = path.parent / ci.DISPLACED_NAME
        self.assertIn("husky", displaced.read_text(encoding="utf-8"))


class TestAHookFromBeforeTheRenameIsOurs(CICase):
    """The project was called founder-os once, and hooks written then say so.

    Every routine here matched only the current marker, so such a hook was invisible as
    ours: never refreshed, and — worse — treated as a stranger's and displaced, chaining
    `exec make check` in front of a body that then ran the suite again. It also predates
    the tree-hash short circuit and the green-run recorder, which is how the optimisation
    shipped in v1.27.0 had never once fired in the repository that wrote it (#146).
    """

    LEGACY = (
        "#!/bin/sh\n"
        "# founder-os pre-push gate\n"
        '_original="$(dirname "$0")/pre-push.founder-os-original"\n'
        'if [ -x "$_original" ]; then "$_original" "$@" || exit $?; fi\n'
        "exec make check\n"
    )

    def founder_os_hook(self):
        from claude_bestpractice import ci

        path = ci.hook_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.LEGACY)
        path.chmod(0o755)
        return path

    def test_it_is_recognised_as_ours(self):
        from claude_bestpractice import ci

        self.founder_os_hook()
        self.assertTrue(ci.installed(self.ctx()))

    def test_an_upgrade_rewrites_it_in_place(self):
        from claude_bestpractice import ci

        path = self.founder_os_hook()
        self.assertTrue(ci.ensure(self.ctx())[0])
        body = path.read_text()
        self.assertIn(ci.MARKER, body)
        self.assertNotIn(ci.FOUNDER_OS_MARKER, body)

    def test_the_rewritten_hook_carries_what_the_old_one_lacked(self):
        """The whole point: the short circuit and the recorder it never had."""
        from claude_bestpractice import ci

        path = self.founder_os_hook()
        ci.ensure(self.ctx())
        body = path.read_text()
        self.assertIn("green-covers-tree", body)
        self.assertIn("record-green", body)

    def test_a_hook_the_old_name_displaced_is_carried_across(self):
        """The old body chained `pre-push.founder-os-original`, the new one chains a
        different filename. Rewriting without moving the file leaves a husky hook on disk,
        unreferenced and silently not running — through the repair meant to prevent it."""
        from claude_bestpractice import ci

        self.founder_os_hook()
        theirs = ci.hooks_dir(self.ctx()) / ci.FOUNDER_OS_DISPLACED_NAME
        theirs.write_text("#!/bin/sh\necho THEIR-CHECK-RAN\nexit 0\n")
        theirs.chmod(0o755)

        ci.ensure(self.ctx())

        carried = ci.hooks_dir(self.ctx()) / ci.DISPLACED_NAME
        self.assertTrue(carried.exists(), "their hook was left where nothing reads it")
        proc = subprocess.run(
            ["sh", str(ci.hook_path(self.ctx()))],
            cwd=str(self.repo), capture_output=True, text=True, timeout=60,
        )
        self.assertIn("THEIR-CHECK-RAN", proc.stdout)

    def test_a_strangers_hook_is_still_displaced_and_never_rewritten(self):
        """Recognising one more marker must not turn into recognising everything."""
        from claude_bestpractice import ci

        path = ci.hook_path(self.ctx())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n# husky\necho THEIR-CHECK-RAN\nexit 0\n")
        path.chmod(0o755)

        self.assertFalse(ci.installed(self.ctx()))
        ci.ensure(self.ctx())
        displaced = ci.hooks_dir(self.ctx()) / ci.DISPLACED_NAME
        self.assertIn("husky", displaced.read_text())

    def test_carrying_never_overwrites_the_arrangement_already_there(self):
        """A file already under the new name IS the current chain. Moving the old one on
        top of it would replace a hook that runs with one that was superseded."""
        from claude_bestpractice import ci

        self.founder_os_hook()
        (ci.hooks_dir(self.ctx()) / ci.FOUNDER_OS_DISPLACED_NAME).write_text("#!/bin/sh\nexit 0\n")
        current = ci.hooks_dir(self.ctx()) / ci.DISPLACED_NAME
        current.write_text("#!/bin/sh\necho CURRENT-CHECK-RAN\nexit 0\n")
        current.chmod(0o755)

        ci.ensure(self.ctx())

        self.assertIn("CURRENT-CHECK-RAN", current.read_text())
