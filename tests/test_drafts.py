"""Auto-drafting decision records from corrections."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase

from claude_bestpractice import drafts


class TestClassification(unittest.TestCase):
    def test_detects_an_explicit_decision(self):
        self.assertEqual(
            drafts.classify("We decided to use Postgres for the ledger after all"), "decision"
        )

    def test_detects_a_rejection(self):
        self.assertEqual(
            drafts.classify("Don't use an ORM here, the query shapes are too irregular"),
            "rejection",
        )

    def test_detects_a_correction(self):
        self.assertEqual(
            drafts.classify("No, not a queue — instead of that, just poll the table"), "correction"
        )

    def test_detects_a_constraint(self):
        self.assertEqual(
            drafts.classify("The export has to be synchronous, the client cannot poll"),
            "constraint",
        )

    def test_ignores_short_turns(self):
        self.assertIsNone(drafts.classify("no"))

    def test_ignores_polite_noise(self):
        """A correction marker without a decision behind it makes the inbox worthless."""
        for noise in ("No thanks, that is fine for now really", "Actually never mind, carry on"):
            with self.subTest(noise=noise):
                self.assertIsNone(drafts.classify(noise))

    def test_ignores_ordinary_instructions(self):
        self.assertIsNone(drafts.classify("Please add a test for the pagination helper"))


class TestExtraction(RepoCase):
    def test_extracts_the_most_recent_first(self):
        turns = [
            "We decided to use SQLite because ops burden matters more than scale here",
            "Don't add a caching layer until we measure something slow",
        ]
        out = drafts.extract(turns, "main", "s1", [])
        self.assertEqual(len(out), 2)
        self.assertIn("caching", out[0].quote)

    def test_caps_per_turn(self):
        turns = [f"We decided thing number {i} because it matters" for i in range(10)]
        self.assertLessEqual(len(drafts.extract(turns, "main", "s1", [])), drafts.MAX_DRAFTS_PER_TURN)

    def test_deduplicates_repeated_wording(self):
        turns = ["We decided to use SQLite for this"] * 4
        self.assertEqual(len(drafts.extract(turns, "main", "s1", [])), 1)

    def test_quote_is_verbatim_not_paraphrased(self):
        turn = "No, not Redis — we already tried that and the ops burden killed us"
        out = drafts.extract([turn], "main", "s1", [])
        self.assertIn("the ops burden killed us", out[0].quote)


class TestInbox(RepoCase):
    def test_record_then_pending(self):
        ctx = self.ctx()
        drafts.record(ctx, drafts.extract(["We decided to ship the CLI first"], "main", "s1", []))
        self.assertEqual(len(drafts.pending(ctx)), 1)

    def test_resolved_drafts_drop_out(self):
        ctx = self.ctx()
        made = drafts.extract(["We decided to ship the CLI first"], "main", "s1", [])
        drafts.record(ctx, made)
        drafts.resolve(ctx, made[0].quote)
        self.assertEqual(drafts.pending(ctx), [])

    def test_render_puts_the_quote_under_why(self):
        draft = {"quote": "we already tried that", "created_at": 0, "subject_paths": []}
        rendered = drafts.render(draft)
        self.assertIn("## Why", rendered)
        self.assertIn("> we already tried that", rendered)
        self.assertIn("## Rejected", rendered)

    def test_next_number_increments(self):
        ctx = self.ctx()
        self.assertEqual(drafts.next_number(ctx), 1)
        self.write(".claude/rules/decisions/0007-x.md", "---\ntitle: X\npaths: '**'\n---\n")
        self.assertEqual(drafts.next_number(ctx), 8)


class TestTranscriptReading(RepoCase):
    def transcript(self, records: list[dict]) -> str:
        path = self.tmp / "t.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return str(path)

    def test_reads_user_turns(self):
        path = self.transcript(
            [
                {"type": "user", "message": {"content": "first thing"}},
                {"type": "assistant", "message": {"content": "reply"}},
                {"type": "user", "message": {"content": [{"type": "text", "text": "second"}]}},
            ]
        )
        self.assertEqual(drafts.user_turns(path), ["first thing", "second"])

    def test_skips_sidechain_turns(self):
        path = self.transcript(
            [{"type": "user", "isSidechain": True, "message": {"content": "subagent noise"}}]
        )
        self.assertEqual(drafts.user_turns(path), [])

    def test_missing_transcript_is_not_fatal(self):
        """The format is internal and changes between releases. Degrade, never raise."""
        self.assertEqual(drafts.user_turns("/nonexistent/x.jsonl"), [])

    def test_garbage_lines_are_skipped(self):
        path = self.tmp / "t.jsonl"
        path.write_text('{"type":"user","message":{"content":"ok"}}\nnot json at all\n')
        self.assertEqual(drafts.user_turns(str(path)), ["ok"])


class TestCli(RepoCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp-decide"), *args],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )

    def test_list_is_empty_initially(self):
        proc = self.run_cli("list")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no pending drafts", proc.stdout)

    def test_accept_writes_a_record_and_clears_the_draft(self):
        ctx = self.ctx()
        drafts.record(
            ctx, drafts.extract(["We decided to use SQLite because ops matter"], "main", "s1", [])
        )
        proc = self.run_cli("accept", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        from claude_bestpractice import knowledge

        files = knowledge.decision_files(ctx)
        self.assertEqual(len(files), 1)
        self.assertIn("We decided to use SQLite", files[0].read_text())
        self.assertEqual(drafts.pending(ctx), [])

    def test_discard_clears_without_writing(self):
        ctx = self.ctx()
        drafts.record(ctx, drafts.extract(["We decided to drop the queue"], "main", "s1", []))
        self.assertEqual(self.run_cli("discard", "1").returncode, 0)
        self.assertEqual(drafts.pending(ctx), [])

        from claude_bestpractice import knowledge

        self.assertEqual(knowledge.decision_files(ctx), [])

    def test_out_of_range_index_is_refused(self):
        self.assertEqual(self.run_cli("accept", "9").returncode, 1)


class TestGateIntegration(RepoCase):
    def test_stop_gate_harvests_drafts(self):
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": "No, not Redis — we already tried that and it broke"},
                }
            )
            + "\n"
        )
        for gate, event in (
            ("session-start", {"hook_event_name": "SessionStart"}),
            (
                "evidence-gate",
                {
                    "hook_event_name": "Stop",
                    "stop_hook_active": False,
                    "transcript_path": str(transcript),
                },
            ),
        ):
            subprocess.run(
                [sys.executable, str(BIN / gate)],
                input=json.dumps({"session_id": "s1", "cwd": str(self.repo), **event}),
                capture_output=True,
                text=True,
                cwd=str(self.repo),
                timeout=120,
            )
        pending = drafts.pending(self.ctx())
        self.assertEqual(len(pending), 1)
        self.assertIn("already tried that", pending[0]["quote"])


if __name__ == "__main__":
    unittest.main()


class TestThePluginDoesNotQuoteItselfBackAsADecision(RepoCase):
    """The inbox filled with the gate's own refusals, and the loop fed itself.

    Claude Code writes hook feedback into the transcript as a `type: "user"` record, and
    this plugin's refusals are full of the words `classify` looks for — "not done yet",
    "must", a list of paths. So every blocked Stop filed the gate's message as a founder
    decision, and the more the gate blocked the more "decisions" appeared. Measured on a
    live repository: 96 drafts, 57 of them the gate quoting itself, 39 the compaction
    preamble, and not one thing a person said.
    """

    GATE_FEEDBACK = (
        "Stop hook feedback: [${CLAUDE_PLUGIN_ROOT}/bin/evidence-gate]: claude-bestpractice "
        "[1/4] — not done yet. Scope drift: backend/src/fuddy/db/migrations/0007_add.py, "
        "backend/src/fuddy/api/orders.py"
    )
    COMPACTION = (
        "This session is being continued from a previous conversation that ran out of "
        "context. The summary below covers the earlier portion of the conversation."
    )
    INTERRUPT = "[Request interrupted by user for tool use]"
    HUMAN = "No, not Postgres — we already tried that and the ops burden killed us"

    def test_the_gates_own_refusal_is_not_a_decision(self):
        self.assertTrue(drafts.is_synthetic(self.GATE_FEEDBACK))
        self.assertIsNone(drafts.classify(self.GATE_FEEDBACK))

    def test_the_compaction_preamble_is_not_a_decision(self):
        self.assertTrue(drafts.is_synthetic(self.COMPACTION))
        self.assertIsNone(drafts.classify(self.COMPACTION))

    def test_an_interrupt_marker_is_not_a_decision(self):
        self.assertTrue(drafts.is_synthetic(self.INTERRUPT))

    def test_a_founder_correction_still_becomes_a_draft(self):
        """The filter has to be specific, or it takes the inbox's only real content."""
        self.assertFalse(drafts.is_synthetic(self.HUMAN))
        self.assertEqual("correction", drafts.classify(self.HUMAN))

    def test_a_transcript_of_the_measured_repository_yields_only_the_human_turn(self):
        transcript = self.repo / "transcript.jsonl"
        rows = [self.GATE_FEEDBACK] * 5 + [self.COMPACTION] * 3 + [self.HUMAN]
        transcript.write_text(
            "\n".join(
                json.dumps({"type": "user", "message": {"content": body}}) for body in rows
            ),
            encoding="utf-8",
        )
        turns = drafts.user_turns(str(transcript))
        self.assertEqual([self.HUMAN], turns)
        self.assertEqual(1, len(drafts.extract(turns, "main", "s1", [])))

    def test_the_phrase_is_only_a_prefix_so_a_founder_quoting_it_is_still_heard(self):
        """Anchored at the start, because a human may well be talking *about* a block."""
        quoted = (
            "We decided to keep the gate: when it says not done yet, that is the point, "
            "even though the stop hook feedback is noisy"
        )
        self.assertFalse(drafts.is_synthetic(quoted))
        self.assertEqual("decision", drafts.classify(quoted))


class TestBothLanguagesOrNeither(unittest.TestCase):
    """The classifier was English-only, so a Russian founder's inbox was reliably EMPTY.

    Worse than the noise #44 removed: an empty inbox reads exactly like a session that
    made no decisions, so there is nothing to notice. Measured five markers out of five
    silent on instructions whose English translations all classified correctly.

    Asserted as a table in both languages, mirroring the branch-type tests, so the
    asymmetry cannot quietly come back the next time a marker is edited.
    """

    SAME_THING_TWICE = [
        ("decision",
         "мы решили использовать Decimal вместо float для денег",
         "we decided to use Decimal instead of float for money"),
        ("rejection",
         "никогда не используй float для денег, только Decimal",
         "never use float for money, only Decimal"),
        ("correction",
         "нет, не так — бери timestamp из source_products",
         "no, take the timestamp from source_products instead"),
        ("constraint",
         "КБЖУ должно быть всегда на 100 г, иначе скоры поедут",
         "the values must always be per 100g, or the scores break"),
        ("constraint",
         "так нельзя, потому что сломается прод — упадут все скоры",
         "we cannot do that because it will break prod"),
    ]

    def test_every_marker_fires_in_russian_and_in_english(self):
        for marker, russian, english in self.SAME_THING_TWICE:
            with self.subTest(marker=marker):
                self.assertEqual(marker, drafts.classify(russian), russian)
                self.assertEqual(marker, drafts.classify(english), english)

    def test_a_rejection_phrased_the_way_people_phrase_it(self):
        """`never` wanted a verb after it, so the natural wording scored nothing.

        Anchored on the comma, which is what carries the rejection — "I have never seen
        this before" is not one and must stay unclassified.
        """
        self.assertEqual(
            "rejection", drafts.classify("store money as Decimal, never float, in every table")
        )
        self.assertIsNone(drafts.classify("I have never seen this behaviour before now"))

    def test_a_standing_instruction_outranks_the_rejection_inside_it(self):
        """"always use X, never Y" is both, and the stronger reading is the true one.

        Markers are ordered by strength and only the strongest is kept. A sentence that
        opens by saying what to do forever is policy that happens to name its alternative,
        not a rejection that happens to be permanent.
        """
        self.assertEqual(
            "standing", drafts.classify("always use Decimal here, never float, because money")
        )

    def test_russian_pleasantries_are_still_noise(self):
        for turn in ("нет, спасибо, это не нужно сейчас делать",
                     "не сейчас, давай позже вернёмся к этому вопросу",
                     "ладно, забудь про это, не будем сейчас трогать"):
            with self.subTest(turn=turn):
                self.assertIsNone(drafts.classify(turn))

    def test_e_and_yo_are_both_accepted(self):
        """Both spellings come off a Russian keyboard; a marker must not depend on it."""
        self.assertEqual("decision", drafts.classify("остановились на Postgres, берем его"))
        self.assertEqual("decision", drafts.classify("остановились на Postgres, берём его"))


class TestARussianTurnReachesTheInbox(RepoCase):
    """End to end: the reported case was a transcript that produced an empty inbox."""

    def test_a_correction_in_russian_becomes_a_draft(self):
        transcript = self.repo / "transcript.jsonl"
        turn = "нет, не так — бери timestamp из source_products, а не из snapshot"
        transcript.write_text(
            json.dumps({"type": "user", "message": {"content": turn}}), encoding="utf-8"
        )
        turns = drafts.user_turns(str(transcript))
        made = drafts.extract(turns, "main", "s1", [])
        self.assertEqual(1, len(made))
        self.assertEqual("correction", made[0].marker)
        self.assertIn("source_products", made[0].quote)


class TestAStandingInstructionIsADecision(unittest.TestCase):
    """Every other marker is correction-shaped, and that missed the commonest class.

    A founder stating a policy calmly — «запомни навсегда», «на будущее», «правило для
    всех чатов», "from now on" — is correcting nothing, so nothing fired. The subsystem
    whose whole job is to stop durable instructions being forgotten was deaf to the exact
    sentence that says "do not forget this". A 500-character message laying out release
    policy for three app stores scored None.
    """

    POLICY = [
        "запомни навсегда: версии во всех трёх сторах держим одинаковые",
        "на будущее: патч ноут пишем по-человечески, а не ллм слопом",
        "всегда пиши патч ноут кратко, как все нормальные приложения",
        "правило для всех чатов: OTA только для JS-изменений, нативка через стор",
        "впредь мажорную версию поднимаем только вместе с нативным изменением",
        "from now on tag every store release with the same version number",
        "as a rule the patch note should be two sentences, not a changelog dump",
        "remember this: OTA is for JS only, native changes go through review",
    ]

    # Description, not policy. Each of these contains a word the marker keys on, which is
    # why they are here: the phrase carries the instruction, the keyword alone does not.
    DESCRIPTION = [
        "это всегда падает на проде, когда база под нагрузкой отвечает медленно",
        "по умолчанию оно берёт последнюю версию, что для нас сейчас неудобно",
        "как правило это занимает минут двадцать, но сейчас почему-то дольше",
        "I do not remember whether we shipped that build to the store last week",
        "I can't remember if the android build was tagged with the same number",
    ]

    def test_a_policy_stated_calmly_is_captured(self):
        for turn in self.POLICY:
            with self.subTest(turn=turn[:40]):
                self.assertEqual("standing", drafts.classify(turn))

    def test_describing_the_world_is_not_stating_a_policy(self):
        for turn in self.DESCRIPTION:
            with self.subTest(turn=turn[:40]):
                self.assertIsNone(drafts.classify(turn))

    def test_the_reported_message_that_scored_nothing(self):
        """Verbatim, because a paraphrase would not prove the thing that failed."""
        turn = (
            "сейчас заранее спрошу и уточню тебе, и так же для всех чатов , у меня в "
            "апстор сейчас релизная v1.0.0, в ру стор тоже самое а в гугл уже на финальной "
            "модерации, и как выйдет я хочу грамотно для всех сразу вести релизы, "
            "обновления и ведения версий по лучшим практикам для этого приложения"
        )
        self.assertEqual("standing", drafts.classify(turn))

    def test_a_question_ending_in_or_not_is_not_a_correction(self):
        """«или нет,» is the tail of a question, and it filed a draft every time."""
        self.assertIsNone(
            drafts.classify("ставили мы там версию или нет, посмотри пожалуйста в конфиге")
        )
        self.assertEqual("correction", drafts.classify("нет, не так — бери из source_products"))


class TestAStandingInstructionSurvivesTheTurn(RepoCase):
    """End to end through the real Stop gate, because the inbox is what the founder opens."""

    def test_it_reaches_the_inbox_and_renders_as_a_record(self):
        from claude_bestpractice import drafts as d

        transcript = self.repo / "transcript.jsonl"
        turn = "запомни навсегда: во всех трёх сторах держим один и тот же номер версии"
        transcript.write_text(
            json.dumps({"type": "user", "message": {"content": turn}}), encoding="utf-8"
        )
        made = d.extract(d.user_turns(str(transcript)), "main", "s1", [])
        self.assertEqual(1, len(made))
        self.assertEqual("standing", made[0].marker)

        d.record(self.ctx(), made)
        pending = d.pending(self.ctx())
        self.assertEqual(1, len(pending))
        self.assertIn("один и тот же номер версии", d.render(pending[0]))


class TestTheQuoteIsNotSilentlyCut(unittest.TestCase):
    """A fragment presented as the whole instruction is a claim nobody can check.

    Issue #41 established that for the prompt gate; the inbox had the same defect in a
    second file. A founder's 522-character release policy was stored as 400 characters
    ending mid-word, and the record put that fragment under "## Why" as their own words.
    """

    def test_a_policy_length_instruction_survives_whole(self):
        turn = (
            "запомни навсегда: у нас три стора, и версия во всех трёх одна и та же — "
            "мажор и минор поднимаем только вместе с нативным изменением, патч ноут "
            "пишем на два предложения по-человечески, а не выгрузкой чейнджлога, "
            "OTA только для JS-изменений, и никакая платформа не уезжает вперёд "
            "остальных без явной причины, которую я называю сам. Исключение только "
            "одно: баг, который воспроизводится на одной платформе и больше нигде — "
            "тогда чиним точечно и догоняем остальные следующим общим релизом, а не "
            "разводим три разные ветки версий по трём сторам"
        )
        self.assertGreater(len(turn), 400, "the fixture proves nothing below the old cap")
        quote = drafts.extract([turn], "main", "s1", [])[0].quote
        self.assertEqual(" ".join(turn.split()), quote)
        self.assertNotIn(drafts.TRUNCATED, quote)

    def test_past_the_cap_the_cut_is_marked(self):
        turn = "запомни: " + ("правило про релизы " * 90)
        quote = drafts.extract([turn], "main", "s1", [])[0].quote
        self.assertIn(drafts.TRUNCATED, quote)
        self.assertTrue(quote.startswith("запомни: правило"))


class TestTheSameCorrectionIsCountedNotRefiled(RepoCase):
    """The extractor re-reads the same recent turns every time it runs, and `record`
    appended unconditionally — so the inbox reached sixty rows carrying four distinct
    sentences, 89KB of them, while `claude-bp status` pointed at it as the next action.
    Nobody reviews a list that is fifteen copies deep.

    Same shape the board already uses for a re-derived finding: the repeat count replaces
    the repeats, and it is the more useful signal — a correction made four times is one
    the founder means.
    """

    def draft(self, quote: str = "никогда не мерджи без моего слова", at: float = 1.0):
        return drafts.Draft("constraint", quote, "main", "s1", at, [])

    def test_filing_it_twice_leaves_one_draft(self):
        ctx = self.ctx()
        self.assertEqual(1, drafts.record(ctx, [self.draft()]))
        self.assertEqual(0, drafts.record(ctx, [self.draft(at=2.0)]),
                         "the same sentence was filed as a second draft")

        waiting = drafts.pending(ctx)
        self.assertEqual(1, len(waiting))
        self.assertEqual(2, waiting[0]["seen"])
        self.assertEqual(1.0, waiting[0]["created_at"], "the first sighting's time was lost")

    def test_a_different_correction_is_still_its_own_draft(self):
        ctx = self.ctx()
        drafts.record(ctx, [self.draft()])
        self.assertEqual(1, drafts.record(ctx, [self.draft("никогда не трогай прод")]))
        self.assertEqual(2, len(drafts.pending(ctx)))
