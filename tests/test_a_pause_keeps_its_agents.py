"""A pause keeps the agents it stops, for the card to say which to resume (decision 0082).

> мне часто надоедает что клод именно стопает агентов полностью и закрывает их когда я прошу
> поставить его всё на паузу

Asked to stop, a session stopped its agents, and told to go on, it started new ones from
nothing. Measured on Claude Code 2.1.319: `TaskStop` answers with the agent's id, `local_agent`
and what it was sent to do, and a `SendMessage` to that id resumed the stopped agent with what it
had been told before.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from helpers import BIN, sid, turn

from claude_bestpractice import plan, sessions, store, subagents

from test_gates import GateCase

AGENT = "a4d2c8f1e0b3a297"
SENT_TO = "research the newest agent harnesses"


def at_work(ident: str = AGENT, said: str = SENT_TO) -> dict:
    """An agent as Stop's `background_tasks` lists one still running."""
    return {"id": ident, "type": "subagent", "status": "running", "description": said,
            "agent_type": "Explore"}


class PauseCase(GateCase):
    WORDS = "ставь всё на паузу, завтра продолжим"

    def setUp(self) -> None:
        super().setUp()
        self.start()
        self.card = self.claim_a_task("s1", "feature.py")
        self.write("feature.py", "def total(prices):\n    return sum(prices)\n")
        self.commit("total, its tests next")
        # Where the founder said it: a pause ends a turn on their words (decision 0075).
        self.transcript = self.tmp / "transcript.jsonl"
        self.transcript.write_text(json.dumps(turn(self.WORDS, time.time())) + "\n", encoding="utf-8")

    def stop(self, session_id: str = "s1", **extra) -> subprocess.CompletedProcess:
        return super().stop(session_id, **{"transcript_path": str(self.transcript), **extra})

    def plan_cli(self, *args: str) -> subprocess.CompletedProcess:
        """`claude-bp-plan`, run by this session the way its shell runs it."""
        env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "s1",
               "PATH": f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}"}
        return subprocess.run(["claude-bp-plan", *args], capture_output=True, text=True,
                              cwd=str(self.repo), env=env, timeout=60)

    def pause(self) -> None:
        proc = self.plan_cli("pause", self.card.id, "--blocker", self.WORDS)
        self.assertEqual(0, proc.returncode, proc.stderr)

    def task_stop(self, ident: str = AGENT, said: str = SENT_TO,
                  kind: str = "local_agent") -> subprocess.CompletedProcess:
        """The PostToolUse of a `TaskStop`, with the result Claude Code 2.1.319 gave one."""
        return self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "TaskStop",
            "tool_input": {"task_id": ident},
            "tool_response": {"message": f"Successfully stopped task: {ident} ({said})",
                              "task_id": ident, "task_type": kind, "command": said},
        })

    def card_now(self) -> plan.Task:
        return plan.find(self.ctx(), self.card.id)

    def settled_after_the_pause(self) -> None:
        """The turn the card was paused in has ended, as the Stop gate stamps an allowed turn."""
        ctx = self.ctx()
        rec = sessions.get(ctx, sid(self.repo, "s1"))
        sessions.touch(ctx, rec.session_id,
                       tool_signatures={**rec.tool_signatures, "_settled_at": time.time() + 2})


class TestAnAgentLeftRunningIsNamed(PauseCase):
    def test_a_pause_over_an_agent_at_work_is_refused_once_naming_the_stop(self):
        self.pause()

        refused = self.stop(background_tasks=[at_work()])

        self.assertEqual(2, refused.returncode, refused.stderr)
        self.assertIn(f"`{AGENT}` ({SENT_TO})", refused.stderr)
        self.assertIn("`TaskStop`", refused.stderr)
        self.assertIn("`SendMessage`", refused.stderr)
        again = self.stop(background_tasks=[at_work()], stop_hook_active=True)
        self.assertEqual(0, again.returncode, "said once for this pause: " + again.stderr)

    def test_a_workflow_or_a_shell_is_not_what_it_asks_about(self):
        self.pause()
        others = [{"id": "w1", "type": "workflow", "status": "running", "description": "review"},
                  {"id": "b2p7qgt0k", "type": "shell", "status": "running", "description": "make check"}]
        self.assertEqual(0, self.stop(background_tasks=others).returncode)

    def test_agents_sent_off_after_an_earlier_pause_are_not_asked_about(self):
        """A card paused in a turn that has ended; the founder has asked for other work since, and
        the agents are that work's."""
        self.pause()
        self.settled_after_the_pause()
        self.task_stop()

        proc = self.stop(background_tasks=[at_work()])

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("`TaskStop`", proc.stderr)
        self.assertNotIn(AGENT, self.card_now().body)

    def test_a_turn_that_is_no_pause_is_not_asked(self):
        """Agents at work spare a finish as before (decision 0070); only a pause stops them."""
        proc = self.stop(background_tasks=[at_work()])
        self.assertNotIn("`TaskStop`", proc.stderr)


class TestTheStoppedAgentsAreOnTheCard(PauseCase):
    def test_the_card_names_each_by_id_and_what_it_was_sent_to_do(self):
        self.pause()
        self.task_stop()

        self.assertEqual(0, self.stop().returncode)

        self.assertIn(plan.STOPPED_AGENTS, self.card_now().body)
        self.assertIn(f"- `{AGENT}`: {SENT_TO}", self.card_now().body)

    def test_claiming_the_card_again_says_them(self):
        self.pause()
        self.task_stop()
        self.stop()

        claimed = self.plan_cli("claim", self.card.id)

        self.assertEqual(0, claimed.returncode, claimed.stderr)
        self.assertIn(plan.STOPPED_AGENTS, claimed.stdout)
        self.assertIn(f"- `{AGENT}`: {SENT_TO}", claimed.stdout)

    def test_the_note_leaves_the_pause_where_it_was_set_down(self):
        """The gate reads the card's stamp as the moment of the pause: a note that moved it took
        back a pause taken after refusals, and let writes made after the pause pass as before."""
        self.pause()
        before = self.card_now().updated_at
        time.sleep(1.1)
        self.task_stop()

        self.stop()

        self.assertIn(AGENT, self.card_now().body)
        self.assertEqual(before, self.card_now().updated_at)

    def test_an_agent_noted_once_is_not_noted_twice(self):
        self.pause()
        self.task_stop()
        self.stop()
        self.task_stop()

        self.stop()

        self.assertEqual(1, self.card_now().body.count(f"`{AGENT}`"))

    def test_a_shell_it_stopped_is_no_agent_to_resume(self):
        self.pause()
        self.task_stop("b2p7qgt0k", "make check", kind="local_bash")

        self.stop()

        self.assertNotIn("b2p7qgt0k", self.card_now().body)

    def test_an_agent_stopped_before_the_last_turn_ended_is_not_this_pauses(self):
        self.task_stop()
        ctx = self.ctx()
        rec = sessions.get(ctx, sid(self.repo, "s1"))
        sessions.touch(ctx, rec.session_id,
                       tool_signatures={**rec.tool_signatures, "_settled_at": time.time() + 0.5})
        time.sleep(1.1)
        self.pause()

        self.stop()

        self.assertNotIn(AGENT, self.card_now().body)

    def test_switched_off_it_keeps_nothing(self):
        """Off means off (decision 0016), for what the PostToolUse hook keeps as well."""
        self.configure(enabled=False)
        self.task_stop()
        self.assertEqual([], subagents.stopped_since(self.ctx(), "s1", 0))


class TestTheRoster(PauseCase):
    def result(self, ident: str, kind: str = "local_agent", said: str = SENT_TO) -> dict:
        return {"task_id": ident, "task_type": kind, "command": said}

    def test_only_a_kind_a_message_resumes_is_kept(self):
        ctx = self.ctx()
        for kind in ("local_bash", "monitor", "in_process_teammate", "workflow"):
            self.assertEqual("", subagents.stopped(ctx, "s1", self.result(f"x-{kind}", kind)))
        self.assertEqual(AGENT, subagents.stopped(ctx, "s1", self.result(AGENT)))
        self.assertEqual([(AGENT, SENT_TO)], subagents.stopped_since(ctx, "s1", 0))

    def test_each_chat_keeps_its_own(self):
        ctx = self.ctx()
        subagents.stopped(ctx, "s1", self.result(AGENT))
        self.assertEqual([], subagents.stopped_since(ctx, "s2", 0))

    def test_it_keeps_the_newest_up_to_its_limit(self):
        ctx = self.ctx()
        for n in range(subagents.ROSTER_LIMIT + 5):
            subagents.stopped(ctx, "s1", self.result(f"a{n:03d}"))
        kept = [ident for ident, _ in subagents.stopped_since(ctx, "s1", 0)]
        self.assertEqual(subagents.ROSTER_LIMIT, len(kept))
        self.assertIn(f"a{subagents.ROSTER_LIMIT + 4:03d}", kept)

    def test_a_result_that_is_no_mapping_keeps_nothing(self):
        for response in (None, "stopped", ["a1"], {"task_type": "local_agent"}):
            self.assertEqual("", subagents.stopped(self.ctx(), "s1", response))
        self.assertFalse(store.tier_b(self.ctx(), subagents.ROSTER_DIR, "s1.json").exists())
