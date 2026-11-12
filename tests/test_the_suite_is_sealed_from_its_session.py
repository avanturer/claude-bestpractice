"""The suite carries nothing of the Claude Code session it runs under.

CI has none of a session's variables, so a test that passes only with one is a test about the
machine. Worse, the variables reach out: a fixture's merge notice, drained through the messaging
socket the suite inherited, arrived in the live session running it as a peer's message:
"[claude-bp] feat/other was merged. Your baseline is behind". Before that, the inherited session
id and child flag made a fixture's server keep its tree (#310).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent

SESSION = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "a-live-session",
    "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/a-live-session.sock",
    "CLAUDE_CODE_MESSAGING_TOKEN": "a-live-token",
    "CLAUDE_CONFIG_DIR": "/home/the-founder/.claude",
    "CLAUDE_PROJECT_DIR": "/home/the-founder/project",
    "CLAUDE_CODE_SOMETHING_A_LATER_VERSION_EXPORTS": "1",
}


def environment_after_helpers(extra: dict[str, str]) -> dict[str, str]:
    """What a test process holds once `helpers` ran, under a session's environment."""
    probe = ("import helpers, json, os\n"
             "print(json.dumps({k: v for k, v in os.environ.items() if k.startswith('CLAUDE')}))")
    proc = subprocess.run([sys.executable, "-c", probe], cwd=str(TESTS), capture_output=True,
                          text=True, timeout=60, env={**os.environ, **extra}, check=True)
    return json.loads(proc.stdout)


class TestNothingOfTheSessionReachesATest(unittest.TestCase):
    def test_no_variable_the_session_exports_survives(self):
        left = environment_after_helpers(SESSION)
        self.assertEqual([], sorted(set(SESSION) & set(left)))

    def test_the_suites_own_switches_stay(self):
        left = environment_after_helpers({"CLAUDE_BESTPRACTICE_ACCEPTANCE_GRACE": "0"})
        self.assertIn("CLAUDE_BESTPRACTICE_NO_AGENTS", left)
        self.assertIn("CLAUDE_BESTPRACTICE_ACCEPTANCE_GRACE", left)
        self.assertIn("CLAUDE_BP_TEST_HOME", left)


if __name__ == "__main__":
    unittest.main()
