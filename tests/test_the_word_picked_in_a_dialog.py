"""Issue #326: the founder picked «+merge» in an `AskUserQuestion` dialog, and the merge gate saw
no word.

A pick starts no turn, so `prompt-capture` never heard it, and the transcript files it as a tool
result, which the reader of the founder's words skips. The merge refusal had told the session to
ask; asked in a dialog, the founder answered into nothing (decision 0094).

The result's shape is Claude Code's documented `AskUserQuestionOutput`: the questions, `answers`
keyed by each question's text, `response` for a reply typed instead, `annotations` for notes,
`afkTimeoutMs` when the dialog closed on its own.
"""

from __future__ import annotations

import json
import time

from helpers import BIN, git, harness_matches, queued, sid
from test_pullrequest import PRCase

from claude_bestpractice import config, evidence, founder, pullrequest

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

    def answered(self, answers: dict, *questions: dict, where: dict | None = None, **extra):
        """The PostToolUse of a dialog the founder answered: `extra` in its result, `where` in
        the event."""
        asked = list(questions) or [question(ASKED, "+merge", "Not yet")]
        return self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse",
            "tool_name": founder.QUESTION_TOOL, "tool_use_id": "toolu_q1",
            "tool_input": {"questions": asked},
            "tool_response": {"questions": asked, "answers": answers, **extra}, **(where or {}),
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
        asked = [question(ASKED, "Show the diff", whole)]
        self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse",
            "tool_name": founder.QUESTION_TOOL, "tool_input": {"questions": asked},
            "tool_response": {"answers": {ASKED: whole}}})
        self.refused()

    def test_a_word_they_typed_and_withheld_is_no_word(self):
        self.answered({ASKED: "не надо +merge"})
        self.refused()

    def test_a_dialog_that_closed_while_they_were_away_is_no_word(self):
        self.answered({ASKED: "+merge"}, afkTimeoutMs=60000)
        self.refused()

    def test_a_result_of_another_shape_is_no_word(self):
        """Its text quotes questions and labels the session wrote, unmarked."""
        self.gate("pr-opened", {
            "session_id": "s1", "hook_event_name": "PostToolUse",
            "tool_name": founder.QUESTION_TOOL, "tool_input": {"questions": [question()]},
            "tool_response": f'User has answered your questions: "{ASKED}"="+merge".',
        })
        self.refused()

    def test_an_answer_with_no_grant_marks_no_message(self):
        """What the chat showed before it is still the next word's to name."""
        self.answered({ASKED: "Not yet"})
        chat = pullrequest._chat(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual([], chat.get("prompts", []))


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
