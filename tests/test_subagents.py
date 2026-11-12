"""A spawn names the model it runs as, and a turn cannot fan out without bound.

Driven through the real `pre-tool` executable rather than the module, because the whole
rule depends on the harness sending the spawn to a PreToolUse hook at all: the tool is
`Agent`, the matcher has to carry it, and a rule that only holds inside a unit test is a
rule the founder pays for and does not have.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import (BIN, RepoCase, a_transcript, harness_matches, reported, sid, spawn_result,
                     spawned)

from claude_bestpractice import sessions, subagents


class SpawnCase(RepoCase):
    def gate(self, tool_input: dict, session_id: str = "s1", **event) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "pre-tool")],
            input=json.dumps({
                "cwd": str(self.repo),
                "session_id": session_id,
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "tool_input": tool_input,
                **event,
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

    def test_a_fork_is_not_asked_for_a_tier(self):
        """Claude Code runs a fork on the parent's model and ignores `model` on the call.

        Refused, the session added a field the harness throws away and spawned again: a round
        trip on every fork, which is on by default in VS Code since 2.1.302.
        """
        self.start()
        decision, why = self.decision(self.gate({"subagent_type": "fork", "prompt": "try the other half"}))
        self.assertNotEqual(decision, "deny", why)
        self.assertEqual(self.record().spawns_this_turn, 1)

    def test_only_the_fork_is_spared(self):
        """An agent of the founder's named `forked-review` is an agent like any other."""
        self.start()
        decision, _ = self.decision(self.gate({"subagent_type": "forked-review", "prompt": "review"}))
        self.assertEqual(decision, "deny")


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

    def test_a_fork_counts_toward_the_ceiling(self):
        """Spared the tier, not the count: a fork is still one more agent working at once."""
        self.start()
        self.spawn(3)
        decision, why = self.decision(self.gate({"subagent_type": "fork", "prompt": "a fourth way"}))
        self.assertEqual(decision, "deny")
        self.assertIn("ceiling is 3", why)

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


class ASpawnThatNeverStartedIsNotCounted(SpawnCase):
    """Three `Agent` calls for an agent type nobody had defined failed inside the tool: no agent
    ran, and the next three spawns of the turn were refused as past the ceiling (#316). Claude
    Code 2.1.322 files such a call's result under the call's id, `is_error`."""

    def setUp(self):
        super().setUp()
        self.start()
        self.transcript = self.tmp / "chat.jsonl"
        self.lines: list[dict] = []

    def spawn(self, call: str, kind: str = "general-purpose") -> tuple[str, str]:
        return self.decision(self.gate(
            {"subagent_type": kind, "model": "sonnet", "prompt": f"work {call}"},
            tool_use_id=call, transcript_path=str(self.transcript)))

    def came_back(self, call: str, **how) -> None:
        now = time.time()
        self.lines += [spawned(call, now), spawn_result(call, now, **how)]
        a_transcript(self.transcript, *self.lines)

    def test_the_spawns_after_three_that_failed_go_through(self):
        for call in ("c1", "c2", "c3"):
            self.assertNotEqual("deny", self.spawn(call, "seo-technical")[0])
            self.came_back(call, error=True)
        for call in ("c4", "c5", "c6"):
            decision, why = self.spawn(call)
            self.assertNotEqual("deny", decision, why)
            self.came_back(call, agent=f"agent-{call}")
        self.assertEqual(3, self.record().spawns_this_turn)
        self.assertEqual(["c4", "c5", "c6"], self.record().spawn_calls)
        decision, why = self.spawn("c7")
        self.assertEqual("deny", decision)
        self.assertIn("already started 3", why)

    def test_spawns_that_started_still_count(self):
        for call in ("c1", "c2", "c3"):
            self.spawn(call)
            self.came_back(call, agent=f"agent-{call}")
        self.assertEqual("deny", self.spawn("c4")[0])

    def test_a_failure_the_transcript_does_not_hold_yet_is_still_counted(self):
        """The harness writes the transcript behind the conversation: what it does not say yet is
        no answer, and the spawn is counted as it always was."""
        for call in ("c1", "c2", "c3"):
            self.spawn(call, "seo-technical")
        self.assertEqual("deny", self.spawn("c4")[0])

    def test_an_error_under_a_call_this_turn_never_counted_gives_nothing_back(self):
        """Another turn's failed spawns were never this turn's to give back."""
        for call in ("o1", "o2", "o3"):
            self.came_back(call, error=True)
        for call in ("c1", "c2", "c3"):
            self.spawn(call)
            self.came_back(call, agent=f"agent-{call}")
        self.assertEqual("deny", self.spawn("c4")[0])

    def test_the_founders_next_message_clears_the_calls_with_the_count(self):
        self.spawn("c1")
        self.assertEqual(["c1"], self.record().spawn_calls)
        self.run_hook("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                         "prompt": "now the next thing"})
        self.assertEqual([], self.record().spawn_calls)


class HowASpawnStandsIsReadOffTheTranscript(unittest.TestCase):
    """The shapes Claude Code 2.1.322 wrote for a spawn and its end (#316, #318)."""

    def setUp(self):
        self.where = Path(tempfile.mkdtemp(prefix="claude-bestpractice-spawns-"))
        self.addCleanup(shutil.rmtree, self.where, ignore_errors=True)
        self.now = time.time()

    def read(self, *entries: dict, since: float = 0.0) -> dict:
        path = a_transcript(self.where / "chat.jsonl", *entries)
        return {call: how for call, (how, _, _) in subagents.standing(str(path), since).items()}

    def test_a_call_that_came_back_an_error_never_started(self):
        self.assertEqual({"c1": subagents.NEVER_STARTED},
                         self.read(spawned("c1", self.now), spawn_result("c1", self.now, error=True)))

    def test_an_agent_sent_to_the_background_is_at_work_until_it_reports(self):
        launched = [spawned("c1", self.now), spawn_result("c1", self.now, agent="a1")]
        self.assertEqual({"c1": subagents.AT_WORK}, self.read(*launched))
        self.assertEqual({"c1": subagents.BACK}, self.read(*launched, reported("c1", self.now)))

    def test_a_report_filed_before_the_result_that_launched_it_still_counts(self):
        self.assertEqual({"c1": subagents.BACK}, self.read(
            spawned("c1", self.now), reported("c1", self.now), spawn_result("c1", self.now, agent="a1")))

    def test_an_agent_in_the_foreground_is_at_work_until_its_answer(self):
        self.assertEqual({"c1": subagents.AT_WORK}, self.read(spawned("c1", self.now)))
        self.assertEqual({"c1": subagents.BACK},
                         self.read(spawned("c1", self.now), spawn_result("c1", self.now)))

    def test_the_old_name_of_the_tool_is_a_spawn_too(self):
        call = spawned("c1", self.now)
        call["message"]["content"][0]["name"] = "Task"
        self.assertEqual({"c1": subagents.AT_WORK}, self.read(call))

    def test_only_a_queued_notification_is_a_report(self):
        """A message that quotes one, or the queue taking one off, ends no agent."""
        quoted = {**reported("c1", self.now), "content": "see <tool-use-id>c1</tool-use-id>"}
        removed = {**reported("c1", self.now), "operation": "remove"}
        launched = [spawned("c1", self.now), spawn_result("c1", self.now, agent="a1")]
        self.assertEqual({"c1": subagents.AT_WORK}, self.read(*launched, quoted, removed))

    def test_a_spawn_from_before_since_is_not_read(self):
        self.assertEqual({"c2": subagents.AT_WORK}, self.read(
            spawned("c1", self.now - 60), spawned("c2", self.now), since=self.now - 1))

    def test_what_an_agent_at_work_was_sent_to_do_is_named_on_one_line(self):
        path = a_transcript(self.where / "chat.jsonl",
                            spawned("c1", self.now, "fix\nthe   parser"),
                            spawned("c2", self.now, "write the docs"),
                            spawn_result("c2", self.now, agent="a2"))
        self.assertEqual(["fix the parser", "write the docs"], subagents.at_work(str(path)))
        self.assertEqual(["fix the parser"], subagents.at_work(str(path), but="a2"))

    def test_only_the_calls_asked_about_are_answered(self):
        path = a_transcript(self.where / "chat.jsonl",
                            spawned("c1", self.now), spawn_result("c1", self.now, error=True),
                            spawned("c2", self.now), spawn_result("c2", self.now, error=True))
        self.assertEqual({"c1"}, subagents.never_started(str(path), ["c1"]))

    def test_a_transcript_that_cannot_be_read_holds_no_spawn(self):
        self.assertEqual({}, subagents.standing(str(self.where / "missing.jsonl")))
        self.assertEqual(set(), subagents.never_started("", ["c1"]))


class TheMatcherCarriesTheTool(unittest.TestCase):
    def test_hooks_json_sends_the_spawn_to_the_gate(self):
        """The rule is worth nothing if the harness never calls the hook for `Agent`."""
        hooks = json.loads((BIN.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
        for name in subagents.SPAWN_TOOLS:
            self.assertTrue(harness_matches(matcher, name), name)


if __name__ == "__main__":
    unittest.main()


class TheAgentIsToldItsCard(SpawnCase):
    """An agent was handed a task and nothing of the work it is part of: the files the session's
    finish measures drift against were invisible to it (decision 0078)."""

    def brief(self) -> str:
        proc = subprocess.run(
            [sys.executable, str(BIN / "subagent-brief")],
            input=json.dumps({"cwd": str(self.repo), "session_id": "s1", "agent_id": "a1",
                              "hook_event_name": "SubagentStart", "agent_type": "general-purpose"}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout).get("hookSpecificOutput", {}).get("additionalContext", "") \
            if proc.stdout.strip() else ""

    def test_the_card_of_the_session_that_started_it_is_in_its_brief(self):
        self.start()
        card = self.claim_a_task("s1", "src/invoice.py")
        body = self.brief()
        self.assertIn(f"works under card {card.id}", body)
        self.assertIn("src/invoice.py", body)

    def test_a_session_that_holds_no_card_hands_none(self):
        self.start()
        self.assertNotIn("works under card", self.brief())
