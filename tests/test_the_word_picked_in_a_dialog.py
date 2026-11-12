"""Issue #326: the founder picked «+merge» in an `AskUserQuestion` dialog, and the merge gate saw
no word.

A pick starts no turn, so `prompt-capture` never heard it, and the transcript files it as a tool
result, which the reader of the founder's words skips. The merge refusal had told the session to
ask; asked in a dialog, the founder answered into nothing (decision 0094).

The result's shape is Claude Code's documented `AskUserQuestionOutput`: the questions, `answers`
keyed by each question's text, `response` for a reply typed instead, `annotations` for notes,
`afkTimeoutMs` when the dialog closed on its own. Claude Code 2.1.322 has the dialog write the
answer into the call's input before the tool runs, so PostToolUse's `tool_input` carries it too,
and only PreToolUse sees the input as the session sent it.
"""

from __future__ import annotations

import json
import time

from helpers import BIN, git, harness_matches, queued, sid
from test_pullrequest import PRCase

from claude_bestpractice import config, evidence, founder, pullrequest, store

ASKED = "Merge #48?"


def question(text: str = ASKED, *labels: str, many: bool = False) -> dict:
    return {"question": text, "header": "Merge", "multiSelect": many,
            "options": [{"label": label, "description": ""} for label in labels]}


class DialogCase(PRCase):
    def setUp(self) -> None:
        super().setUp()
        git(["remote", "add", "origin", "https://github.com/o/r.git"], self.repo)
        self.write("src/app.py", "x = 1\n")
        self.commit("add the app module")
        evidence.record_green(self.ctx(), ["pytest"])
        self.start()
        self.open_a_pr()

    def asked(self, *questions: dict, call: str = "toolu_q1", **sent):
        """The PreToolUse of a dialog's call, as the session sent it."""
        return self.gate("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse",
            "tool_name": founder.QUESTION_TOOL, "tool_use_id": call,
            "tool_input": {"questions": list(questions) or [question(ASKED, "+merge", "Not yet")],
                           **sent}})

    def answered(self, answers: dict, *questions: dict, where: dict | None = None,
                 call: str = "toolu_q1", ask: bool = True, result=None, **extra):
        """A dialog the founder answered: its call asked, then its PostToolUse, whose input holds
        the answer as the dialog wrote it in. `extra` is in its result, `where` in the event, and
        `result` replaces the result whole."""
        asked = list(questions) or [question(ASKED, "+merge", "Not yet")]
        if ask:
            self.asked(*asked, call=call)
        return self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse",
            "tool_name": founder.QUESTION_TOOL, "tool_use_id": call,
            "tool_input": {"questions": asked, "answers": answers},
            "tool_response": {"questions": asked, "answers": answers, **extra}
            if result is None else result, **(where or {}),
        })

    def merge(self, number: int = PRCase.PR_NUMBER, **where):
        return self.ran("Bash", {"command": f"gh pr merge {number} --squash"}, **where)

    def merged(self, number: int = PRCase.PR_NUMBER) -> None:
        proc = self.merge(number)
        self.assertNotEqual("deny", self.decision(proc), self.reason(proc))

    def refused(self, number: int = PRCase.PR_NUMBER) -> None:
        self.assertEqual("deny", self.decision(self.merge(number)))


class TestTheWordPickedInADialogIsTheFoundersWord(DialogCase):
    def test_the_report(self):
        self.answered({ASKED: "+merge"})
        self.merged()

    def test_it_is_recorded_as_a_word_of_theirs_said_in_this_chat(self):
        before = time.time()
        self.answered({ASKED: "+merge"})
        self.assertGreaterEqual(
            founder.last_said(self.ctx(), sid(self.repo, "s1"), config.APPROVE_MERGE), before)

    def test_each_word_picked_in_one_question_counts(self):
        self.answered({ASKED: "+merge, +release"}, question(ASKED, "+merge", "+release", many=True))
        self.assertTrue(config.approved(self.ctx(), config.APPROVE_RELEASE))
        self.merged()

    def test_what_they_typed_under_other_is_read_as_their_message(self):
        self.answered({ASKED: "+merge, выглядит хорошо"}, question(ASKED, "Show the diff", "Later"))
        self.merged()

    def test_a_pick_beside_what_they_typed(self):
        self.answered({ASKED: "Not yet, +merge"}, question(ASKED, "Not yet", "Later", many=True))
        self.merged()

    def test_picks_sent_as_a_list(self):
        self.answered({ASKED: ["Not yet", "+merge"]})
        self.merged()

    def test_a_reply_typed_instead_of_an_answer(self):
        self.answered({}, response="+merge")
        self.merged()

    def test_a_note_typed_beside_an_answer(self):
        self.answered({ASKED: "Not yet"}, annotations={ASKED: {"notes": "передумал, +merge"}})
        self.merged()

    def test_it_names_what_the_question_named(self):
        """As a typed word names what the message it answers named (decision 0066): #1140, which
        no session here opened, is put to them by the question."""
        git(["branch", "fix/timeout"], self.repo)
        pullrequest.opened(self.ctx(), "fix/timeout", "main", "", number=1140,
                           url="https://github.com/o/r/pull/1140")
        both = "Merge #48 and #1140?"
        self.answered({both: "+merge"}, question(both, "+merge", "Not yet"))
        self.merged()
        self.merged(1140)


class TestOnlyTheirAnswerCounts(DialogCase):
    def test_an_option_they_did_not_pick_is_no_word(self):
        self.answered({ASKED: "Not yet"})
        self.refused()

    def test_a_question_carrying_the_word_is_no_word(self):
        asked = "Say +merge to merge #48?"
        self.answered({asked: "Not yet"}, question(asked, "Not yet", "Later"))
        self.refused()

    def test_a_label_holding_the_word_in_a_sentence_is_no_word(self):
        """The session writes the labels: picked for the diff, it would merge."""
        whole = "Show the diff, then +merge"
        self.answered({ASKED: whole}, question(ASKED, "Show the diff", whole))
        self.refused()
        for joined in (f"{whole}, ok", f"{whole},ok"):
            with self.subTest(joined=joined):
                self.answered({ASKED: joined}, question(ASKED, "Show the diff", whole, many=True))
                self.refused()

    def test_a_result_that_does_not_echo_the_questions_is_read_against_those_asked(self):
        whole = "Show the diff, then +merge"
        self.answered({ASKED: whole}, question(ASKED, "Show the diff", whole),
                      result={"answers": {ASKED: whole}})
        self.refused()

    def test_a_word_they_typed_and_withheld_is_no_word(self):
        self.answered({ASKED: "не надо +merge"})
        self.refused()

    def test_a_dialog_that_closed_while_they_were_away_is_no_word(self):
        self.answered({ASKED: "+merge"}, afkTimeoutMs=60000)
        self.refused()

    def test_a_result_of_another_shape_is_no_word(self):
        """Its text quotes questions and labels the session wrote, unmarked."""
        self.answered({ASKED: "+merge"},
                      result=f'User has answered your questions: "{ASKED}"="+merge".')
        self.refused()

    def test_an_answer_with_no_grant_marks_no_message(self):
        """What the chat showed before it is still the next word's to name."""
        self.answered({ASKED: "Not yet"})
        chat = pullrequest._chat(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual([], chat.get("prompts", []))


class TestTheSessionCannotAnswerForThem(DialogCase):
    """A call carrying an answer of its own puts nothing to the founder, and its answer would come
    back as theirs."""

    def test_a_call_that_carries_an_answer_is_refused(self):
        for field, value in (("answers", {ASKED: "+merge"}), ("response", "+merge"),
                             ("annotations", {ASKED: {"notes": "+merge"}})):
            with self.subTest(field=field):
                proc = self.asked(**{field: value})
                self.assertEqual("deny", self.decision(proc))
                self.assertIn("Answers are the founder's to give in the dialog", self.reason(proc))
                self.assertIn(f"`{field}`", self.reason(proc))

    def test_a_call_that_only_asks_is_let_through_unjudged(self):
        for sent in ({}, {"answers": {}}):
            with self.subTest(sent=sent):
                self.assertIsNone(self.decision(self.asked(**sent)))

    def test_a_question_asked_again_is_judged_by_nothing_else_here(self):
        """Asked once more after «Not yet», it is a question to a person, not a call repeated."""
        for _ in range(5):
            self.assertIsNone(self.decision(self.asked()))

    def test_an_answer_to_a_dialog_pre_tool_never_saw_is_no_word(self):
        self.answered({ASKED: "+merge"}, ask=False)
        self.refused()

    def test_an_answer_to_a_call_it_refused_is_no_word(self):
        self.asked(answers={ASKED: "+merge"})
        self.answered({ASKED: "+merge"}, ask=False)
        self.refused()

    def test_each_call_is_heard_once(self):
        founder.asked_without_answers(self.ctx(), "toolu_q9")
        self.assertTrue(founder.was_asked_without_answers(self.ctx(), "toolu_q9"))
        self.assertFalse(founder.was_asked_without_answers(self.ctx(), "toolu_q9"))
        founder.asked_without_answers(self.ctx(), "")
        self.assertFalse(founder.was_asked_without_answers(self.ctx(), ""))

    def test_a_note_a_day_old_is_let_go(self):
        notes = store.tier_b(self.ctx(), founder.ASKED)
        store.write_json(notes, {"toolu_old": time.time() - 90000})
        founder.asked_without_answers(self.ctx(), "toolu_new")
        self.assertEqual(["toolu_new"], list(store.read_json(notes)))

    def test_a_note_file_of_another_shape_is_begun_again(self):
        store.write_json(store.tier_b(self.ctx(), founder.ASKED), [])
        self.assertFalse(founder.was_asked_without_answers(self.ctx(), "toolu_q9"))
        founder.asked_without_answers(self.ctx(), "toolu_q9")
        self.assertTrue(founder.was_asked_without_answers(self.ctx(), "toolu_q9"))

    def test_pre_tool_hears_the_call(self):
        hooks = json.loads((BIN.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        entry = hooks["hooks"]["PreToolUse"][0]
        self.assertTrue(harness_matches(entry["matcher"], founder.QUESTION_TOOL))
        self.assertIn("pre-tool", entry["hooks"][0]["command"])


class TestItIsATurnOfTheirs(DialogCase):
    """The pull requests the chat showed since the founder's previous message, which a sent word
    names (decision 0043), are counted the same way around a word picked in a dialog."""

    def view(self, number: int) -> None:
        self.tool("Bash", {"command": f"gh pr view {number} --json title"})

    def test_what_was_shown_before_it_is_named_by_it_and_not_by_the_next(self):
        self.view(936)
        self.answered({ASKED: "+release"}, question(ASKED, "+release", "Not yet"))
        self.accept()
        self.refused(936)

    def test_a_message_sent_while_it_worked_starts_its_window(self):
        transcript = self.tmp / "transcript.jsonl"
        self.view(936)
        transcript.write_text(json.dumps(queued("936 не трогай", time.time())) + "\n")
        self.view(941)
        self.answered({ASKED: "+merge"}, where={"transcript_path": str(transcript)})
        self.merged(941)
        self.refused(936)


class TestTheGateSaysSo(DialogCase):
    def test_the_hook_that_hears_it_runs_after_the_dialog(self):
        hooks = json.loads((BIN.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        entries = hooks["hooks"]["PostToolUse"]
        self.assertTrue(harness_matches(entries[0]["matcher"], founder.QUESTION_TOOL))
        self.assertIn("pr-opened", entries[0]["hooks"][0]["command"])
        self.assertFalse(harness_matches(entries[0]["matcher"], "AskUserQuestions"))

    def test_the_refusal_says_a_pick_in_a_dialog_counts(self):
        said = self.reason(self.merge())
        self.assertIn("picked in an `AskUserQuestion` dialog", said)
        self.assertIn("either counts", said)
