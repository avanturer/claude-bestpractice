"""A block from this plugin is the session's to resolve, never the founder's to hear first.

Both halves are proven through the real executables: the instruction attached to the
refusal, and the Stop gate that refuses a turn which ended on a block nothing was done
about. A rule that only holds in the message text is a rule the model may read; this one
has a gate under it.
"""

from __future__ import annotations

import json
import unittest

from helpers import BIN, RepoCase, sid

from claude_bestpractice import defects, sessions


class RefusalCase(RepoCase):
    def start(self, session_id: str = "s1"):
        return self.run_hook(
            "session-start",
            {"session_id": session_id, "hook_event_name": "SessionStart", "source": "startup"},
        )

    def refused_write(self, session_id: str = "s1"):
        """A write with a credential in it — refused by a rule nobody argues with."""
        return self.run_hook("pre-tool", {
            "session_id": session_id,
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.repo / "src" / "config.py"),
                "content": 'API_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n',
            },
        })

    def write_to_protected_state(self, session_id: str = "s1"):
        """A refusal that genuinely waits on a person: the config is the founder's file."""
        return self.run_hook("pre-tool", {
            "session_id": session_id,
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.repo / ".claude" / "claude-bestpractice" / "config.json"),
                "content": "{}",
            },
        })

    def stop(self, session_id: str = "s1"):
        return self.run_hook("evidence-gate", {
            "session_id": session_id, "hook_event_name": "Stop", "stop_hook_active": False,
        })

    def reason(self, proc) -> str:
        if not proc.stdout.strip():
            return ""
        return json.loads(proc.stdout).get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )

    def record(self, session_id: str = "s1"):
        return sessions.get(self.ctx(), sid(self.repo, session_id))

    def run_cli(self, name: str, *args: str):
        import subprocess
        import sys

        return subprocess.run(
            [sys.executable, str(BIN / name), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )


class TheRefusalSaysWhoseItIs(RefusalCase):
    def test_a_refusal_carries_the_standing_instruction(self):
        self.start()
        why = self.reason(self.refused_write())

        self.assertIn("A GATE refused this, not the founder", why)
        self.assertIn("claude-bp-report defect", why)

    def test_the_way_out_it_names_is_a_command_that_runs(self):
        """Card 0067: three refusals sent the session to a command that does not exist.
        A named escape that errors is worse than none, so the escape is executed here."""
        proc = self.run_cli(
            "claude-bp-report", "defect",
            "the path gate read an SQL operator as a redirect and refused a psql call",
            "--gate", "pre-tool",
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(defects.unsent(self.ctx())))

    def test_a_report_that_says_nothing_is_not_recorded(self):
        """And the exit code says so. It was 0, which a session reads as "filed"."""
        proc = self.run_cli("claude-bp-report", "defect", "broken")
        self.assertEqual(1, proc.returncode)
        self.assertIn("say what was refused", proc.stdout)
        self.assertEqual([], defects.unsent(self.ctx()))

    def test_a_refusal_the_founder_owns_carries_no_instruction(self):
        """`protect_trunk` and the merge approval genuinely wait on a person. Telling a
        session to resolve those itself would be this plugin arguing with decision 0006.
        """
        self.start()
        why = self.reason(self.write_to_protected_state())
        self.assertIn("enforcement state", why)
        self.assertNotIn("A GATE refused this, not the founder", why)


class TheTurnCannotEndOnAnUnansweredBlock(RefusalCase):
    def test_a_turn_that_ends_on_a_refusal_is_blocked_once(self):
        self.start()
        self.refused_write()

        proc = self.stop()
        self.assertEqual(2, proc.returncode, proc.stdout)
        self.assertIn("ending on a block nothing was done about", proc.stderr)
        self.assertIn("claude-bp-report defect", proc.stderr)

    def test_it_is_asked_once_and_then_carried(self):
        """One unignorable interruption per refusal, like the pull-request hand-off. A
        session that ignores it, crashes, or hits the ceiling must not meet it again."""
        self.start()
        self.refused_write()
        self.stop()

        self.assertEqual({}, self.record().refusal)

    def test_doing_something_about_it_clears_it(self):
        """The refusal is answered by acting, whichever way the session decided. Here it
        writes the file without the credential, which is the gate having been right."""
        self.start()
        self.refused_write()
        self.run_hook("pre-tool", {
            "session_id": "s1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.repo / "src" / "config.py"),
                "content": "API_TOKEN = os.environ['API_TOKEN']\n",
            },
        })

        proc = self.stop()
        self.assertNotIn("ending on a block nothing was done about", proc.stderr)

    def test_a_founders_refusal_leaves_nothing_for_the_stop_gate_to_ask(self):
        """"Tell the founder and stop" IS the finish for those, and a gate that then
        refuses the stop would be two rules of this plugin deadlocking on each other."""
        self.start()
        self.write_to_protected_state()

        proc = self.stop()
        self.assertNotIn("ending on a block nothing was done about", proc.stderr)


if __name__ == "__main__":
    unittest.main()
