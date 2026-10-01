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


# Claude Code 2.1.286's idle notice, as the CLI writes it — read out of the binary.
IDLE_NOTICE = (
    '[Cross-session idle notice] "fuddy-8b", which you asked to be notified about, is idle '
    "now — it finished a turn at 13:05. This is an automated notice from that session's "
    "harness — not a message from a person, and not an instruction; act on it only "
    "insofar as your user's earlier request calls for it."
)


class TestOnlyWorkOpensACard(GateCase):
    """Issue #256. Twenty-one of one board's 156 open cards were messages that were not work:
    «статус», «тебя можно закрывать?», «не в тот чат отправил», an idle notice. Every first
    message opened one, whatever it said, and so did every question after it."""

    def say(self, prompt: str) -> None:
        self.gate("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                     "prompt": prompt})

    def titles(self) -> list[str]:
        return [task.title for task in plan.load_all(self.ctx(), plan.NEXT)]

    def setUp(self) -> None:
        super().setUp()
        self.start()

    def test_a_first_message_that_is_not_work_opens_none(self):
        self.say("статус")
        self.assertEqual([], self.titles())
        self.assertEqual("статус", sessions.get(self.ctx(), sid(self.repo, "s1")).task_statement,
                         "the session still says what it was told")

    def test_a_question_opens_none(self):
        for asked in ("тебя можно закрывать?", "что осталось сделать"):
            with self.subTest(asked=asked):
                self.say(asked)
                self.assertEqual([], self.titles())

    def test_the_idle_notice_is_the_harness_and_opens_none(self):
        self.say(IDLE_NOTICE)
        self.assertEqual([], self.titles())
        self.assertEqual("", sessions.get(self.ctx(), sid(self.repo, "s1")).task_statement)

    def test_an_instruction_still_opens_one(self):
        self.say("почини импортер, он падает на пустом csv")
        self.assertEqual(["почини импортер, он падает на пустом csv"], self.titles())

    def test_a_question_about_a_file_is_about_that_file(self):
        self.write("tests/test_api.py", "def test_empty():\n    assert True\n")
        self.say("почему падает tests/test_api.py на пустом ответе?")
        self.assertEqual(["почему падает tests/test_api.py на пустом ответе?"], self.titles())

    def test_a_question_does_not_rename_the_card(self):
        self.say("почини импортер, он падает на пустом csv")
        self.say("тебя можно закрывать?")
        self.assertEqual(["почини импортер, он падает на пустом csv"], self.titles())

    def test_a_card_the_session_wrote_on_keeps_its_title(self):
        """The login-code budget alarm renamed by the next message, its subject left in the
        body."""
        self.say("почини импортер, он падает на пустом csv")
        card = plan.load_all(self.ctx(), plan.NEXT)[0]
        plan.amend(self.ctx(), card.id, note="the importer reads the header row twice")
        self.say("ещё добавь лог на каждую пропущенную строку")
        self.assertEqual(["почини импортер, он падает на пустом csv"], self.titles())


class TestACardNoWorkFollowedIsWithdrawn(GateCase):
    """The other half of #256: a card a message opened stayed in `next` for weeks, and nobody
    ever closed one."""

    def setUp(self) -> None:
        super().setUp()
        self.start()
        self.gate("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                     "prompt": "посмотри новый репорт из мониторинга"})
        self.card = plan.load_all(self.ctx(), plan.NEXT)[0]

    def test_a_turn_that_did_not_plan_it_withdraws_it(self):
        self.stop()
        self.assertEqual([], plan.load_all(self.ctx(), plan.NEXT))

    def test_withdrawn_is_moved_aside_and_its_number_is_not_reused(self):
        self.stop()
        aside = plan.plan_dir(self.ctx(), plan.WITHDRAWN) / self.card.path.name
        self.assertTrue(aside.is_file(), "a withdrawn card is moved, never deleted")
        self.assertGreater(int(plan.next_id(self.ctx())), int(self.card.id))

    def test_a_card_the_session_planned_stays(self):
        plan.amend(self.ctx(), self.card.id, paths=["src/report.py"], done_when="report parsed")
        self.stop()
        self.assertEqual([self.card.id], [task.id for task in plan.load_all(self.ctx(), plan.NEXT)])

    def test_another_sessions_card_is_not_this_ones_to_withdraw(self):
        self.start("s2")
        self.stop("s2")
        self.assertEqual([self.card.id], [task.id for task in plan.load_all(self.ctx(), plan.NEXT)])

    def test_the_session_can_name_it(self):
        named = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), "update", self.card.id,
             "--title", "Разобрать отчёт мониторинга за ночь"],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60)
        self.assertEqual(0, named.returncode, named.stderr)
        self.assertEqual("Разобрать отчёт мониторинга за ночь", plan.find(self.ctx(), self.card.id).title)


class TestTheUpgradeWithdrawsWhatOlderVersionsOpened(GateCase):
    """Repair 0039: the rule a Stop now applies, applied once to the cards already there."""

    def a_card_opened_by(self, opener: str, **planned):
        card = plan.add(self.ctx(), "статус", source=plan.FROM_THE_FOUNDER, opened_by=opener)
        if planned:
            plan.amend(self.ctx(), card.id, **planned)
        return card

    def test_one_whose_session_is_gone_is_withdrawn(self):
        from claude_bestpractice import migrate

        self.a_card_opened_by("a-session-that-is-gone")
        said = migrate._withdraw_messages_that_were_never_work(self.ctx())
        self.assertEqual([], plan.load_all(self.ctx(), plan.NEXT))
        self.assertIn("1 card(s)", said)

    def test_one_a_live_session_opened_waits_for_its_stop(self):
        from claude_bestpractice import migrate

        self.start()
        self.a_card_opened_by("s1")
        migrate._withdraw_messages_that_were_never_work(self.ctx())
        self.assertEqual(1, len(plan.load_all(self.ctx(), plan.NEXT)))

    def test_one_somebody_wrote_on_stays(self):
        from claude_bestpractice import migrate

        self.a_card_opened_by("a-session-that-is-gone", note="the real task: budget alarm")
        migrate._withdraw_messages_that_were_never_work(self.ctx())
        self.assertEqual(1, len(plan.load_all(self.ctx(), plan.NEXT)))
