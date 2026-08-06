"""What the plugin ORDERS, it does not then make the founder authorise.

Measured on a real machine: 35 classifier blocks over three days, three of which were
production actions worth asking about. The rest was the plugin's own instructions coming
back as prompts — the interruption the whole design exists to remove (#99).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase, sid

from claude_bestpractice import vouch, worktree


class TestTheLineIsReadAsCommands(unittest.TestCase):
    """Never as text. `echo "git worktree add"` is a sentence about a worktree."""

    def test_the_compound_form_the_refusal_itself_suggests(self):
        self.assertIn("worktree", vouch.for_bash("cd /tmp/x && git worktree add ../t -b b", []))

    def test_the_subcommand_survives_a_global_option(self):
        """`git -C x worktree add` and `git worktree add` are one command."""
        self.assertIn("worktree", vouch.for_bash("git -C /tmp/x worktree add ../t", []))

    def test_quoting_a_worktree_command_is_not_running_one(self):
        self.assertEqual("", vouch.for_bash('echo "git worktree add ../t"', []))

    def test_one_command_it_cannot_name_takes_the_whole_line(self):
        """`allow_tool` approves the LINE; there is no half of it to approve."""
        self.assertEqual("", vouch.for_bash("git worktree add ../t && ssh prod 'deploy'", []))

    def test_a_line_it_cannot_tokenise_vouches_for_nothing(self):
        """A line crafted to break the tokeniser must not become a line that walks past."""
        self.assertEqual("", vouch.for_bash("git worktree add 'unterminated", []))

    def test_the_suite_is_vouched_for_exactly_as_detected(self):
        command = ["python3", "-m", "pytest", "-q"]
        self.assertIn("evidence gate", vouch.for_bash("python3 -m pytest -q", command))

    def test_a_modified_suite_command_is_not_the_one_that_was_demanded(self):
        """Defensible to run, and not the thing this plugin asked for. That gap is the rule."""
        command = ["python3", "-m", "pytest", "-q"]
        self.assertEqual("", vouch.for_bash("python3 -m pytest -q --pdb", command))
        self.assertEqual("", vouch.for_bash("python3 -m pytest -q", []))

    def test_production_stays_out(self):
        for line in (
            "ssh prod 'systemctl restart api'",
            "git push --force origin main",
            "eas update --branch production",
            "npx vercel deploy --prod",
        ):
            self.assertEqual("", vouch.for_bash(line, ["make", "test"]), line)

    def test_navigation_alone_orders_nothing(self):
        self.assertEqual("", vouch.for_bash("cd /tmp/x", ["make", "test"]))


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
        return {
            "session_id": "s1", "hook_event_name": "PreToolUse",
            "tool_name": "Bash", "tool_input": {"command": command},
        }

    def test_the_worktree_command_the_gate_orders_needs_no_permission(self):
        proc = self.gate(self.bash("git worktree list"))
        self.assertEqual("allow", self.decision(proc), proc.stdout + proc.stderr)

    def test_an_unvouched_command_is_left_to_the_permission_layer(self):
        """Silence is a different answer from allow: it leaves the normal flow deciding."""
        proc = self.gate(self.bash("curl -X POST https://api.example.com/deploy"))
        self.assertIsNone(self.decision(proc), proc.stdout)

    def provisioned(self):
        """A tree this plugin made for this session — the scene the vouch is written for."""
        return worktree.provision(self.ctx(), "fix the importer", sid(self.repo, "s1"))

    def write_into(self, made, name: str, content: str) -> dict:
        return {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(made / name), "content": content},
        }

    def test_writing_in_the_tree_this_plugin_handed_the_session_needs_no_permission(self):
        """The gate refused a write for not being in a worktree, then made one, then let
        the permission layer interrogate every write into it."""
        made = self.provisioned()
        self.assertIsNotNone(made, "provisioning failed; the test proves nothing")
        proc = self.gate(self.write_into(made, "src.py", "x = 1\n"))
        self.assertEqual("allow", self.decision(proc), proc.stdout + proc.stderr)

    def test_a_vouch_never_overrides_this_gate_s_own_refusal(self):
        """The whole safety of the design: the vouch is read after every rule has spoken.

        This write is in a tree the plugin provisioned for this very session, so the vouch
        WOULD approve it — and the credential scan refuses it anyway. Were the vouch read
        any earlier, `allow_tool` would be a way past this gate rather than the last word
        on a call it had already decided to allow.
        """
        made = self.provisioned()
        self.assertIsNotNone(made, "provisioning failed; the test proves nothing")
        payload = 'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
        self.assertEqual(vouch.WRITE, vouch.for_write(
            self.ctx(), sid(self.repo, "s1"), [made / "config.py"]),
            "the fixture proves nothing: this path was not vouchable to begin with")

        proc = self.gate(self.write_into(made, "config.py", payload))
        self.assertEqual("deny", self.decision(proc), proc.stdout + proc.stderr)

    def test_a_sibling_s_tree_is_not_vouched_for(self):
        """`provisioned_for` is narrow on purpose: made by us, for the session asking."""
        made = worktree.provision(self.ctx(), "someone else's work", "another-session")
        self.assertIsNotNone(made)
        self.assertEqual("", vouch.for_write(self.ctx(), sid(self.repo, "s1"), [made / "a.py"]))

if __name__ == "__main__":
    unittest.main()
