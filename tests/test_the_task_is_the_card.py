"""The task the scope-drift gate measures against is the card on the board, not the last message.

Reported as #236 from two long sessions on 1.69.4. In one, a `/goal` was running and a card
was claimed; the founder asked a question in passing — «а ты же всё по v2 системе скоринга
делаешь?» — and at the next Stop the gate measured a build config the goal needed against
that question and asked for it to be reverted. In the other, the gate quoted the session's
first message as the task, three compactions and one claimed card later.

Driven through the real hooks: `prompt-capture` for what the founder says, `claude-bp-plan`
for the card, `evidence-gate` for the verdict.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import time

from helpers import BIN, sid

from claude_bestpractice import plan, sessions

from test_gates import JUNIT_PASS, GateCase

QUESTION = "а ты же все по v2 системе скоринга делаешь, а не по старой?"
CONFIG = "experiments/training/configs/v34.yaml"


class TheCase(GateCase):
    def say(self, prompt: str):
        return self.gate("prompt-capture", {"session_id": "s1",
                                            "hook_event_name": "UserPromptSubmit",
                                            "prompt": prompt})

    def plan_cli(self, *args: str) -> subprocess.CompletedProcess:
        """`claude-bp-plan` as the session runs it: the harness's session id in the env."""
        import os

        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
            env={**os.environ, "CLAUDE_CODE_SESSION_ID": "s1"},
        )

    def drift_in(self, proc) -> str:
        return next((part for part in proc.stderr.split("\n\n") if "Scope drift" in part), "")

    def setUp(self) -> None:
        super().setUp()
        self.write("src/score.py", "VERSION = 1\n")
        self.write(CONFIG, "data_hash: old\n")
        self.commit("a project to work on")
        self.start()
        # A message that names a file, which is what turns the drift check on at all.
        self.say("/goal довести v4 до готовности: src/score.py считает по v2")


class TestTheCardIsTheTask(TheCase):
    def test_a_file_on_the_card_is_not_drift_after_a_question(self):
        """The report itself: goal set, card claimed naming the config, a question asked,
        the config changed. The question is not the task, and the card says the config is."""
        self.claim_a_task("s1", "src/score.py", CONFIG)
        self.say(QUESTION)
        self.write("src/score.py", "VERSION = 2\n")
        self.write(CONFIG, "data_hash: new\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)

        proc = self.stop()
        self.assertEqual("", self.drift_in(proc), proc.stderr)
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_the_refusal_quotes_the_card_and_not_the_question(self):
        card = self.claim_a_task("s1", "src/score.py")
        self.say(QUESTION)
        self.write(CONFIG, "data_hash: new\n")

        drift = self.drift_in(self.stop())
        self.assertIn(CONFIG, drift)
        self.assertIn(f"Task: {card.id} {card.title}", drift)
        self.assertNotIn("v2 системе", drift)

    def test_the_way_out_is_the_sessions_own_and_it_works(self):
        """Decisions 0014 and 0020: the refusal names a command the session can run, and
        running it as printed is enough. "Ask the founder to name those paths" was the
        remedy, which handed them a question about a hook."""
        card = self.claim_a_task("s1", "src/score.py")
        self.write("src/score.py", "VERSION = 2\n")
        self.write(CONFIG, "data_hash: new\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)

        drift = self.drift_in(self.stop())
        self.assertNotIn("ask the founder", drift)
        line = next((ln.strip() for ln in drift.splitlines()
                     if ln.strip().startswith(f"claude-bp-plan update {card.id} --paths")), "")
        self.assertTrue(line, drift)

        ran = self.plan_cli(*shlex.split(line)[1:])
        self.assertEqual(0, ran.returncode, ran.stderr)
        self.assertEqual({"src/score.py", CONFIG}, set(plan.find(self.ctx(), card.id).paths),
                         "the command dropped what the card already named")
        proc = self.stop()
        self.assertEqual("", self.drift_in(proc), proc.stderr)

    def test_a_card_this_session_closed_still_covers_its_files(self):
        card = self.claim_a_task("s1", "src/score.py", CONFIG)
        plan.complete(self.ctx(), card.id, sid(self.repo, "s1"))
        self.write(CONFIG, "data_hash: new\n")
        self.assertEqual("", self.drift_in(self.stop()))

    def test_a_siblings_card_covers_nothing_here(self):
        """The board widens THIS session's scope by what THIS session declared on it."""
        self.start("s2")
        task = plan.add(self.ctx(), "someone else's work", paths=[CONFIG], done_when="x")
        plan.claim(self.ctx(), task.id, sid(self.repo, "s2"), self.ctx().branch)
        self.claim_a_task("s1", "src/score.py")
        self.write(CONFIG, "data_hash: new\n")
        self.assertIn(CONFIG, self.drift_in(self.stop()))

    def test_without_a_card_the_statement_is_still_quoted(self):
        self.write(CONFIG, "data_hash: new\n")
        drift = self.drift_in(self.stop())
        self.assertIn("Task was: довести v4 до готовности", drift)
        self.assertIn("claude-bp-plan", drift)


class TestACardNeverTurnsTheCheckOn(GateCase):
    """What the founder said named no file, so there is nothing to measure against, and a card
    only widens a scope that exists. Letting it switch the check on would refuse turns that
    passed before it was read at all — in a release that fixes refusals."""

    def test_no_path_in_any_message_still_means_no_drift(self):
        self.write("src/score.py", "VERSION = 1\n")
        self.commit("a project")
        self.start()
        self.gate("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                     "prompt": "доведи скоринг до ума, пожалуйста"})
        self.claim_a_task("s1", "src/score.py")
        self.write("src/other.py", "x = 1\n")
        self.assertNotIn("Scope drift", self.stop().stderr)


class TestTheGoalCommandIsNotTheInstruction(TheCase):
    """`/goal <condition>` reaches `UserPromptSubmit` as typed — measured on 2.1.282."""

    def statement(self) -> str:
        return sessions.get(self.ctx(), sid(self.repo, "s1")).task_statement

    def test_the_condition_is_the_statement(self):
        self.assertEqual("довести v4 до готовности: src/score.py считает по v2",
                         self.statement())

    def test_its_paths_are_still_collected(self):
        rec = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(["src/score.py"], rec.task_paths)

    def test_the_card_it_opens_is_titled_by_the_condition(self):
        titles = [task.title for task in plan.load_all(self.ctx(), plan.NEXT)]
        self.assertEqual(["довести v4 до готовности: src/score.py считает по v2"], titles)

    def test_clearing_it_or_asking_its_status_instructs_nothing(self):
        before = self.statement()
        for said in ("/goal", "/goal clear", "/goal cancel", "/goal  OFF "):
            with self.subTest(said=said):
                self.say(said)
                self.assertEqual(before, self.statement())

    def test_a_new_goal_replaces_the_old_one(self):
        self.say("/goal все тесты в tests/ проходят и линтер чист")
        self.assertEqual("все тесты в tests/ проходят и линтер чист", self.statement())


class TestWithoutTheGoalCommand(GateCase):
    def test_what_is_not_the_command_comes_back_as_it_came(self):
        for text in ("/goals for this quarter are in docs/", "  fix the /goal parser ",
                     "/goalkeeper", ""):
            with self.subTest(text=text):
                self.assertEqual(text, sessions.without_the_goal_command(text))

    def test_the_command_comes_off_and_the_condition_stays(self):
        self.assertEqual("tests pass", sessions.without_the_goal_command("/goal tests pass"))
        self.assertEqual("tests\npass", sessions.without_the_goal_command("/goal\ntests\npass"))
        self.assertEqual("", sessions.without_the_goal_command("/goal reset"))
