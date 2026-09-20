"""A spawn names the model it runs as, and a turn cannot fan out without bound.

Driven through the real `pre-tool` executable rather than the module, because the whole
rule depends on the harness sending the spawn to a PreToolUse hook at all: the tool is
`Agent`, the matcher has to carry it, and a rule that only holds inside a unit test is a
rule the founder pays for and does not have.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase, sid

from claude_bestpractice import sessions, subagents


class SpawnCase(RepoCase):
    def gate(self, tool_input: dict, session_id: str = "s1") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "pre-tool")],
            input=json.dumps({
                "cwd": str(self.repo),
                "session_id": session_id,
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "tool_input": tool_input,
            }),
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )

    def start(self, session_id: str = "s1") -> None:
        subprocess.run(
            [sys.executable, str(BIN / "session-start")],
            input=json.dumps({
                "cwd": str(self.repo),
                "session_id": session_id,
                "hook_event_name": "SessionStart",
                "source": "startup",
            }),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )

    def decision(self, result: subprocess.CompletedProcess) -> tuple[str, str]:
        if not result.stdout.strip():
            return "", ""
        payload = json.loads(result.stdout).get("hookSpecificOutput", {})
        return payload.get("permissionDecision", ""), payload.get("permissionDecisionReason", "")

    def record(self, session_id: str = "s1"):
        return sessions.get(self.ctx(), sid(self.repo, session_id))


class TierIsNamed(SpawnCase):
    def test_spawn_without_a_model_is_refused(self):
        self.start()
        decision, why = self.decision(self.gate({"subagent_type": "Explore", "prompt": "find it"}))
        self.assertEqual(decision, "deny")
        self.assertIn("does not say what model it runs as", why)
        # The refusal has to be actionable on its own: a session that has never read the
        # skill still needs to know which tier answers for which work.
        for tier in subagents.TIERS:
            self.assertIn(tier, why)

    def test_a_named_tier_passes(self):
        self.start()
        decision, _ = self.decision(
            self.gate({"subagent_type": "Explore", "model": "haiku", "prompt": "find it"})
        )
        self.assertNotEqual(decision, "deny")

    def test_a_full_model_id_is_a_named_tier(self):
        self.start()
        decision, _ = self.decision(
            self.gate({"subagent_type": "Explore", "model": "claude-haiku-4-5-20251001"})
        )
        self.assertNotEqual(decision, "deny")

    def test_inherit_on_the_call_is_not_a_choice(self):
        """`inherit` IS the default, spelled out. Accepting it would make the gate a word."""
        self.start()
        decision, _ = self.decision(self.gate({"subagent_type": "Plan", "model": "inherit"}))
        self.assertEqual(decision, "deny")

    def test_a_definition_that_pins_a_tier_needs_nothing_on_the_call(self):
        self.write(
            ".claude/agents/reviewer.md",
            "---\nname: reviewer\ndescription: reviews\nmodel: sonnet\n---\n\nReview it.\n",
        )
        self.start()
        decision, _ = self.decision(self.gate({"subagent_type": "reviewer", "prompt": "review"}))
        self.assertNotEqual(decision, "deny")

    def test_a_definition_that_says_inherit_is_still_refused(self):
        self.write(
            ".claude/agents/helper.md",
            "---\nname: helper\ndescription: helps\nmodel: inherit\n---\n\nHelp.\n",
        )
        self.start()
        decision, why = self.decision(self.gate({"subagent_type": "helper"}))
        self.assertEqual(decision, "deny")
        self.assertIn("parent's model spelled out", why)

    def test_a_directory_traversal_in_the_agent_name_reads_nothing(self):
        self.assertEqual(subagents.pinned_tier(self.ctx(), "../../etc/passwd"), "")
        self.assertEqual(subagents.pinned_tier(self.ctx(), ""), "")


class CountIsBounded(SpawnCase):
    def spawn(self, n: int, first: int = 0) -> list[tuple[str, str]]:
        """n spawns, each asking something different.

        Different prompts on purpose: identical calls in a row are refused by the repeat
        detector, which is a separate rule and would answer these tests instead of the
        one they are about.
        """
        return [
            self.decision(self.gate(
                {"subagent_type": "Explore", "model": "haiku", "prompt": f"find {i}"}
            ))
            for i in range(first, first + n)
        ]

    def test_the_fourth_spawn_of_a_turn_is_refused(self):
        self.start()
        allowed = self.spawn(3)
        self.assertNotIn("deny", [d for d, _ in allowed])
        decision, why = self.decision(self.gate(
            {"subagent_type": "Explore", "model": "haiku", "prompt": "one more"}
        ))
        self.assertEqual(decision, "deny")
        self.assertIn("ceiling is 3", why)
        # Named as the founder's switch, never as something this session can raise.
        self.assertIn("subagent_fanout", why)

    def test_a_refused_spawn_is_not_counted(self):
        """A call this gate stopped is not work the session did, here as everywhere."""
        self.start()
        self.gate({"subagent_type": "Explore"})  # refused: no tier
        self.assertEqual(self.record().spawns_this_turn, 0)

    def test_the_founders_ceiling_is_read(self):
        self.configure(subagent_fanout=1)
        self.start()
        self.spawn(1)
        decision, why = self.decision(self.gate(
            {"subagent_type": "Explore", "model": "haiku", "prompt": "and another"}
        ))
        self.assertEqual(decision, "deny")
        self.assertIn("ceiling is 1", why)

    def test_zero_switches_the_ceiling_off(self):
        self.configure(subagent_fanout=0)
        self.start()
        decisions = self.spawn(5)
        self.assertNotIn("deny", [d for d, _ in decisions])

    def test_the_budget_starts_again_on_the_founders_next_message(self):
        self.start()
        self.spawn(3)
        subprocess.run(
            [sys.executable, str(BIN / "prompt-capture")],
            input=json.dumps({
                "cwd": str(self.repo), "session_id": "s1",
                "hook_event_name": "UserPromptSubmit", "prompt": "now do the next thing",
            }),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )
        self.assertEqual(self.record().spawns_this_turn, 0)
        decision, _ = self.decision(self.gate(
            {"subagent_type": "Explore", "model": "haiku", "prompt": "the next thing"}
        ))
        self.assertNotEqual(decision, "deny")


class TheMatcherCarriesTheTool(unittest.TestCase):
    def test_hooks_json_sends_the_spawn_to_the_gate(self):
        """The rule is worth nothing if the harness never calls the hook for `Agent`."""
        hooks = json.loads((BIN.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
        for name in subagents.SPAWN_TOOLS:
            self.assertIn(name, matcher.split("|"))


if __name__ == "__main__":
    unittest.main()
