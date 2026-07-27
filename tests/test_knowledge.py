"""The DECIDED layer: caps, anchor integrity, decision records, the subagent brief."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase

from claude_bestpractice import knowledge

GOOD_PRODUCT = """# Product

## What this is
A ledger for freelancers.

## Who it is for
Solo contractors invoicing under ten clients.

## Non-goals
- Payroll
- Multi-currency
- Anything requiring an accountant to operate

## Current priority
Invoice PDF export.
"""

GOOD_ENTITIES = """Invoice:
  what: a billable document sent to one client
  code: Invoice @ src/models.py
  invariants: total equals the sum of line items
  depends_on: Client
  breaks_if_wrong: clients are billed twice
Client:
  what: the person who receives invoices
  code: Client @ src/models.py
  invariants: email is unique
  depends_on: none
  breaks_if_wrong: invoices go to the wrong inbox
Payment:
  what: money received against an invoice
  code: Payment @ src/models.py
  invariants: never exceeds the invoice total
  depends_on: Invoice
  breaks_if_wrong: revenue is overstated
"""

MODELS = "class Invoice:\n    pass\n\n\nclass Client:\n    pass\n\n\nclass Payment:\n    pass\n"


class KnowledgeCase(RepoCase):
    def seed(self, product: str = GOOD_PRODUCT, entities: str = GOOD_ENTITIES) -> None:
        self.write("src/models.py", MODELS)
        self.write(f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}", product)
        self.write(f"{knowledge.DOMAIN_DIR}/{knowledge.ENTITIES}", entities)

    def problems(self) -> list[str]:
        return [str(p) for p in knowledge.validate(self.ctx())]


class TestEntityParsing(unittest.TestCase):
    def test_parses_names_and_keys(self):
        entities = knowledge.parse_entities(GOOD_ENTITIES)
        self.assertEqual([e.name for e in entities], ["Invoice", "Client", "Payment"])
        self.assertEqual(entities[0].code, "Invoice @ src/models.py")
        self.assertIn("sum of line items", entities[0].invariants)

    def test_ignores_comments_and_blank_lines(self):
        entities = knowledge.parse_entities("# a comment\n\nA:\n  what: thing\n")
        self.assertEqual([e.name for e in entities], ["A"])

    def test_unknown_keys_are_dropped(self):
        entities = knowledge.parse_entities("A:\n  what: thing\n  nonsense: x\n")
        self.assertEqual(entities[0].what, "thing")
        self.assertFalse(hasattr(entities[0], "nonsense"))


class TestAnchors(KnowledgeCase):
    def test_resolving_anchor_passes(self):
        self.seed()
        self.assertEqual(self.problems(), [])

    def test_renamed_symbol_breaks_the_anchor(self):
        """The whole point: a rename fails loudly instead of describing a ghost."""
        self.seed()
        self.write("src/models.py", MODELS.replace("class Invoice:", "class Bill:"))
        problems = self.problems()
        self.assertTrue(any("no longer resolves" in p for p in problems), problems)

    def test_moved_file_breaks_the_anchor(self):
        self.seed()
        (self.repo / "src" / "models.py").unlink()
        self.assertTrue(any("no longer resolves" in p for p in self.problems()))

    def test_bare_path_anchor_is_accepted(self):
        self.seed(entities=GOOD_ENTITIES.replace("Invoice @ src/models.py", "src/models.py"))
        self.assertEqual(self.problems(), [])


class TestCaps(KnowledgeCase):
    def test_product_without_non_goals_is_refused(self):
        self.seed(product="# Product\n\n## What this is\nA thing.\n")
        self.assertTrue(any("non-goals" in p for p in self.problems()))

    def test_oversized_product_is_refused(self):
        self.seed(product=GOOD_PRODUCT + "\n".join(f"line {i}" for i in range(80)))
        self.assertTrue(any("over 60 lines" in p for p in self.problems()))

    def test_too_many_entities_is_refused(self):
        blocks = "".join(
            f"E{i}:\n  what: w\n  code: src/models.py\n  invariants: i\n"
            f"  depends_on: d\n  breaks_if_wrong: b\n"
            for i in range(15)
        )
        self.seed(entities=blocks)
        self.assertTrue(any("entities; keep between" in p for p in self.problems()))

    def test_missing_entity_key_is_refused(self):
        self.seed(entities="Invoice:\n  what: a doc\n  code: Invoice @ src/models.py\n")
        self.assertTrue(any("missing" in p for p in self.problems()))

    def test_prescriptive_glossary_line_is_refused(self):
        self.seed()
        self.write(
            f"{knowledge.RULES_DIR}/{knowledge.GLOSSARY}",
            "# Glossary\nRun — you should always use this term.\n",
        )
        self.assertTrue(any("prescriptive" in p for p in self.problems()))

    def test_overlong_glossary_line_is_refused(self):
        self.seed()
        self.write(
            f"{knowledge.RULES_DIR}/{knowledge.GLOSSARY}",
            "# Glossary\nRun — " + "x" * 200 + "\n",
        )
        self.assertTrue(any("over 160 chars" in p for p in self.problems()))


class TestDecisions(KnowledgeCase):
    def decision(self, name: str, body: str) -> None:
        self.write(f"{knowledge.DECISIONS_DIR}/{name}", body)

    def test_valid_record_passes(self):
        self.seed()
        self.decision(
            "0001-use-sqlite.md",
            "---\ntitle: Use SQLite\npaths: src/**\n---\n\n## Decision\nSQLite.\n\n"
            "## Why\nOne process, no ops.\n\n## Rejected\n- Postgres: needs a server we will not run.\n",
        )
        self.assertEqual(self.problems(), [])

    def test_record_without_rejected_section_is_refused(self):
        self.seed()
        self.decision(
            "0001-x.md", "---\ntitle: X\npaths: src/**\n---\n\n## Decision\nX.\n\n## Why\nBecause.\n"
        )
        self.assertTrue(any("Rejected" in p for p in self.problems()))

    def test_record_without_paths_is_refused(self):
        """Without a path scope it loads in every session forever."""
        self.seed()
        self.decision(
            "0001-x.md",
            "---\ntitle: X\n---\n\n## Decision\nX.\n\n## Why\nY.\n\n## Rejected\n- Z: no.\n",
        )
        self.assertTrue(any("no `paths:`" in p for p in self.problems()))

    def test_paths_glob_matching_nothing_is_refused(self):
        self.seed()
        self.decision(
            "0001-x.md",
            "---\ntitle: X\npaths: gone/**\n---\n\n## Decision\nX.\n\n## Why\nY.\n\n"
            "## Rejected\n- Z: no.\n",
        )
        self.assertTrue(any("matches nothing" in p for p in self.problems()))

    def test_oversized_record_is_refused(self):
        self.seed()
        body = "---\ntitle: X\npaths: src/**\n---\n\n## Decision\nX.\n\n## Why\nY.\n\n## Rejected\n"
        body += "\n".join(f"- alt{i}: no" for i in range(60))
        self.decision("0001-x.md", body)
        self.assertTrue(any("over 40 lines" in p for p in self.problems()))


class TestIndex(KnowledgeCase):
    def test_index_lists_records(self):
        self.seed()
        self.write(
            f"{knowledge.DECISIONS_DIR}/0001-a.md",
            "---\ntitle: First\npaths: src/**\n---\n\n## Rejected\n- x: no\n",
        )
        index = knowledge.build_index(self.ctx())
        self.assertIn("First", index)
        self.assertIn("0001-a.md", index)

    def test_superseded_records_drop_out_of_the_index(self):
        """Retired by a later record, never deleted. The history stays true."""
        self.seed()
        self.write(
            f"{knowledge.DECISIONS_DIR}/0001-old.md",
            "---\ntitle: Old way\npaths: src/**\n---\n\n## Rejected\n- x: no\n",
        )
        self.write(
            f"{knowledge.DECISIONS_DIR}/0002-new.md",
            "---\ntitle: New way\npaths: src/**\nsupersedes: 0001\n---\n\n## Rejected\n- x: no\n",
        )
        index = knowledge.build_index(self.ctx())
        self.assertNotIn("Old way", index)
        self.assertIn("New way", index)
        self.assertTrue((self.repo / knowledge.DECISIONS_DIR / "0001-old.md").exists())

    def test_index_respects_its_line_cap(self):
        self.seed()
        for i in range(30):
            self.write(
                f"{knowledge.DECISIONS_DIR}/{i:04d}-d.md",
                f"---\ntitle: D{i}\npaths: src/**\n---\n\n## Rejected\n- x: no\n",
            )
        lines = knowledge.build_index(self.ctx()).splitlines()
        self.assertLessEqual(len(lines), knowledge.INDEX_MAX_LINES)


class TestSubagentBrief(KnowledgeCase):
    def test_carries_non_goals_and_entities(self):
        self.seed()
        brief = knowledge.subagent_brief(self.ctx())
        self.assertIn("Payroll", brief)
        self.assertIn("Invoice", brief)
        self.assertIn("breaks if wrong", brief)

    def test_respects_its_budget(self):
        self.seed()
        self.assertLessEqual(len(knowledge.subagent_brief(self.ctx(), max_chars=200)), 200)

    def test_empty_layer_yields_an_empty_brief(self):
        self.assertEqual(knowledge.subagent_brief(self.ctx()), "")


class TestCli(KnowledgeCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bestpractice-knowledge"), *args],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )

    def test_init_scaffolds_then_validate_reports_the_gaps(self):
        proc = self.run_cli("init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).exists())
        # Templates are placeholders, so validation must NOT pass yet.
        self.assertEqual(self.run_cli("validate").returncode, 1)

    def test_init_is_idempotent(self):
        self.run_cli("init")
        self.write(f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}", GOOD_PRODUCT)
        self.run_cli("init")
        self.assertIn("Payroll", (self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).read_text())

    def test_validate_passes_on_a_complete_layer(self):
        self.seed()
        proc = self.run_cli("validate")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("valid", proc.stdout)


class TestSubagentHook(KnowledgeCase):
    def hook(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "subagent-brief")],
            input=json.dumps(
                {
                    "session_id": "s1",
                    "hook_event_name": "SubagentStart",
                    "cwd": str(self.repo),
                    "agent_type": "Explore",
                }
            ),
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=60,
        )

    def test_injects_the_brief(self):
        self.seed()
        proc = self.hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Payroll", body)

    def test_stays_silent_when_there_is_nothing_to_say(self):
        proc = self.hook()
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("additionalContext", proc.stdout)


if __name__ == "__main__":
    unittest.main()
