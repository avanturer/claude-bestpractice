"""The hook payloads Claude Code sends carry every field this plugin reads.

`fixtures/hook-payloads.json` is what the Claude Code installed here sent to every event one
headless session reaches, captured by `tools/capture_payloads.py`: field names, never their
values. Recaptured after an upgrade, a field the harness renamed fails here, instead of in a
gate that reads "" for it in a founder's session and lets the call through.
"""

from __future__ import annotations

import json
import unittest

from helpers import REPO_ROOT

CAPTURED = json.loads((REPO_ROOT / "tests" / "fixtures" / "hook-payloads.json").read_text())
HOOKS = json.loads((REPO_ROOT / "plugin" / "hooks" / "hooks.json").read_text())

# What each gate reads off the event, by the event it is registered for.
READS = {
    "SessionStart": {"session_id", "cwd", "source", "transcript_path", "model"},
    "UserPromptSubmit": {"session_id", "cwd", "prompt", "prompt_id", "transcript_path"},
    "PreToolUse": {"session_id", "cwd", "tool_name", "tool_input", "tool_use_id",
                   "transcript_path"},
    "PostToolUse": {"session_id", "cwd", "tool_name", "tool_input", "tool_response",
                    "tool_use_id"},
    "SubagentStart": {"cwd"},
    "PreCompact": {"session_id", "cwd", "transcript_path", "trigger"},
    "Stop": {"session_id", "cwd", "transcript_path", "stop_hook_active"},
}
# And inside a call, what the gates read of its input and of its result.
PARTS = {
    ("PreToolUse", "Bash", "tool_input"): {"command"},
    ("PreToolUse", "Write", "tool_input"): {"file_path", "content"},
    ("PreToolUse", "Agent", "tool_input"): {"subagent_type", "prompt", "description"},
    ("PostToolUse", "Bash", "tool_input"): {"command"},
    ("PostToolUse", "Bash", "tool_response"): {"stdout", "stderr"},
}
# Registered, and out of one headless session's reach: each has its own test of what it reads.
NOT_CAPTURED = {
    "Setup": "fires on `claude --init`, which a capture does not run",
    "WorktreeCreate": "replaces git's own worktree creation, so a probe on it breaks the session",
    "PermissionDenied": "fires only when auto mode's classifier refuses a call",
}


class TestTheHarnessSendsWhatTheGatesRead(unittest.TestCase):
    def test_every_event_carries_the_fields_its_gate_reads(self):
        for event, fields in READS.items():
            with self.subTest(event=event):
                sent = set(CAPTURED["events"][event]["keys"])
                self.assertEqual(set(), fields - sent, f"{event} no longer sends these")

    def test_every_call_carries_what_its_gate_reads_inside_it(self):
        for (event, tool, part), fields in PARTS.items():
            with self.subTest(event=event, tool=tool, part=part):
                sent = set(CAPTURED["events"][event]["tools"][tool][part])
                self.assertEqual(set(), fields - sent)

    def test_every_event_the_plugin_listens_to_was_captured_or_says_why_not(self):
        listened = set(HOOKS["hooks"])
        self.assertEqual(set(), listened - set(CAPTURED["events"]) - set(NOT_CAPTURED))
        self.assertEqual(set(), set(READS) - listened, "a read for an event nothing listens to")

    def test_it_says_which_claude_code_sent_them(self):
        self.assertRegex(CAPTURED["claude_code"], r"^\d+\.\d+\.\d+$")
