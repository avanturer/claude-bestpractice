"""End-to-end: a whole project lifecycle, driven only through the real gate executables.

Unit tests prove each mechanism. This proves they compose — that a session which starts,
plans, works, collides with a sibling, gets blocked, fixes it and finishes leaves the
repository in a state the next session can actually use.

Nothing here calls into the library except to assert. Every action goes through the same
executables the harness runs, with the same event shapes, in a real git repository.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest

from helpers import BIN, RepoCase, git

from claude_bestpractice import board, conflicts, knowledge, plan, provenance, sessions

JUNIT_PASS = '<?xml version="1.0"?><testsuite name="s" tests="6" failures="0" errors="0"></testsuite>'
JUNIT_FAIL = '<?xml version="1.0"?><testsuite name="s" tests="6" failures="2" errors="0"></testsuite>'


class Scenario(RepoCase):
    """A founder starting a real product, one turn at a time."""

    def hook(self, name: str, **event) -> subprocess.CompletedProcess:
        return self.run_hook(name, event)

    def context_of(self, proc: subprocess.CompletedProcess) -> str:
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return ""

    def decision_of(self, proc: subprocess.CompletedProcess):
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def cli(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / name), *args],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=180,
        )

    def build_app(self) -> None:
        """A small but real product: models, an API, billing."""
        self.write(
            "package.json",
            '{"name":"ledger","dependencies":{"express":"^4.19.0"},'
            '"scripts":{"test":"vitest run"}}',
        )
        self.write(
            "src/models.js",
            "export class Invoice { constructor(id) { this.id = id } }\n"
            "export class Client { constructor(email) { this.email = email } }\n",
        )
        self.write(
            "src/api.js",
            "import { Invoice, Client } from './models.js'\n"
            "export function createInvoice(c) { return new Invoice(c) }\n"
            "export function findClient(e) { return new Client(e) }\n",
        )
        self.write(
            "src/billing.js",
            "import { Invoice } from './models.js'\n"
            "export function total(i) { return new Invoice(i.id) }\n",
        )
        self.commit("the product")


class TestFullLifecycle(Scenario):
    # Each phase is its own method so a failure names the phase that broke, and so the
    # orchestrating test below reads as the narrative it is meant to prove.

    def phase_onboard(self) -> None:
        setup = self.hook("setup", session_id="s1", hook_event_name="Setup")
        self.assertEqual(setup.returncode, 0, setup.stderr)
        brief = self.context_of(setup)
        self.assertIn("Detected stage", brief)
        self.assertIn("waiting on a human answer", brief)

        entities = knowledge.load(self.ctx()).entities
        self.assertTrue(entities, "nothing derived from a repository with real types")
        for entity in entities:
            self.assertTrue(
                knowledge.anchor_resolves(self.ctx(), entity.code),
                f"{entity.name} anchored to something that does not exist",
            )
        # The product itself is never invented — a fabricated one would be believed.
        self.assertIn(
            "ANSWER THIS", (self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).read_text()
        )

    def phase_founder_answers(self) -> None:
        self.write(
            f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}",
            "# Product\n\n## What this is\nInvoicing for solo contractors.\n\n"
            "## Who it is for\nFreelancers with under ten clients.\n\n"
            "## Non-goals\n- Payroll\n- Multi-currency\n- Anything needing an accountant\n\n"
            "## Current priority\nCSV export.\n",
        )

    def phase_plan_and_start(self) -> None:
        self.cli("claude-bp-plan", "add", "Add CSV export to billing")
        self.assertEqual(plan.summary(self.ctx())["next"], 1)

        board_text = self.context_of(
            self.hook("session-start", session_id="s1", hook_event_name="SessionStart")
        )
        self.assertIn("Add CSV export", board_text)
        self.assertIn("health:", board_text)
        self.assertIn("stage: prototype", board_text)

        self.cli("claude-bp-plan", "claim", "0001", "--session", "s1")
        self.hook(
            "prompt-capture",
            session_id="s1",
            hook_event_name="UserPromptSubmit",
            prompt="Add CSV export to src/billing.js",
        )
        record = sessions.get(self.ctx(), "s1")
        self.assertEqual(record.task_statement, "Add CSV export to src/billing.js")
        self.assertIn("src/billing.js", record.task_paths)

    def phase_credential_refused(self) -> None:
        leaked = self.hook(
            "pre-tool",
            session_id="s1",
            hook_event_name="PreToolUse",
            tool_name="Write",
            tool_input={
                "file_path": str(self.repo / "src" / "keys.js"),
                "content": "export const KEY = 'sk_live_abcdefghijklmnopqrs'\n",
            },
        )
        self.assertEqual(self.decision_of(leaked), "deny")
        self.assertFalse((self.repo / "src" / "keys.js").exists())
        # A refused write never happened, so it must not appear as work done.
        self.assertNotIn("src/keys.js", sessions.get(self.ctx(), "s1").last_touched)

    def phase_evidence_demanded(self) -> None:
        blocked = self.hook(
            "evidence-gate", session_id="s1", hook_event_name="Stop", stop_hook_active=False
        )
        self.assertEqual(blocked.returncode, 2)
        # `npm test` is configured but vitest is not installed in the fixture, so the
        # gate cannot witness a run and falls back to demanding an artifact. Both the
        # unrunnable case and the no-artifact case must block, and neither may claim the
        # tests failed — a missing runner is a setup problem, not a red suite.
        self.assertIn("not accepted as evidence", blocked.stderr)
        self.assertIn("vitest", blocked.stderr, "the hint must match this project's stack")
        self.assertNotIn("The suite FAILS", blocked.stderr)

        self.write("junit.xml", JUNIT_FAIL)
        still = self.hook(
            "evidence-gate", session_id="s1", hook_event_name="Stop", stop_hook_active=False
        )
        self.assertEqual(still.returncode, 2)
        self.assertIn("4/6 passed", still.stderr)

    def phase_green_finish(self) -> None:
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        allowed = self.hook(
            "evidence-gate", session_id="s1", hook_event_name="Stop", stop_hook_active=False
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

        self.cli("claude-bp-plan", "done", "0001")
        counts = plan.summary(self.ctx())
        self.assertEqual((counts["doing"], counts["done"]), (0, 1))

    def phase_next_session_inherits(self) -> None:
        self.commit("csv export")
        board_text = self.context_of(
            self.hook("session-start", session_id="s2", hook_event_name="SessionStart")
        )
        self.assertIn("(1 done)", board_text)

    def test_a_project_goes_from_nothing_to_a_verified_finish(self):
        """The whole arc, through the real executables, in a real repository."""
        self.build_app()
        self.phase_onboard()
        self.phase_founder_answers()
        self.phase_plan_and_start()
        self.phase_credential_refused()
        self.write(
            "src/billing.js",
            (self.repo / "src/billing.js").read_text() + "export function toCsv(i) { return i.id }\n",
        )
        self.phase_evidence_demanded()
        self.phase_green_finish()
        self.phase_next_session_inherits()

    def test_scope_drift_is_caught_before_the_turn_ends(self):
        self.build_app()
        self.hook("setup", session_id="s1", hook_event_name="Setup")
        self.hook("session-start", session_id="s1", hook_event_name="SessionStart")
        self.hook(
            "prompt-capture",
            session_id="s1",
            hook_event_name="UserPromptSubmit",
            prompt="Fix the rounding in src/billing.js",
        )

        self.write("src/billing.js", "export function total() { return 1 }\n")
        self.write("src/api.js", "export function sneaky() { return 2 }\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)

        blocked = self.hook(
            "evidence-gate", session_id="s1", hook_event_name="Stop", stop_hook_active=False
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("Scope drift", blocked.stderr)
        self.assertIn("src/api.js", blocked.stderr)


class TestTwoSessionsInParallel(Scenario):
    def test_they_see_each_other_and_cannot_collide(self):
        self.build_app()
        self.hook("setup", session_id="alpha", hook_event_name="Setup")

        self.hook("session-start", session_id="alpha", hook_event_name="SessionStart")
        beta_board = self.context_of(
            self.hook("session-start", session_id="beta", hook_event_name="SessionStart")
        )
        self.assertIn("OTHER LIVE SESSIONS (1)", beta_board)

        edit = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo / "src" / "billing.js"), "new_string": "x"},
        }
        self.assertIsNone(self.decision_of(self.hook("pre-tool", session_id="alpha", **edit)))

        collision = self.hook("pre-tool", session_id="beta", **edit)
        self.assertEqual(self.decision_of(collision), "deny")
        reason = json.loads(collision.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("alpha", reason)

        # A different file is never contested.
        other = dict(edit)
        other["tool_input"] = {"file_path": str(self.repo / "src" / "api.js"), "new_string": "y"}
        self.assertIsNone(self.decision_of(self.hook("pre-tool", session_id="beta", **other)))

    def test_a_dead_session_does_not_block_the_living(self):
        self.build_app()
        ctx = self.ctx()
        sessions.register(ctx, self.session_record("ghost", pid=999_999_999))
        sessions.acquire_lease(ctx, "ghost", "src/billing.js")
        task = plan.add(ctx, "orphaned work")
        plan.claim(ctx, task.id, "ghost", ctx.branch)

        self.hook("session-start", session_id="alive", hook_event_name="SessionStart")

        self.assertIsNone(sessions.get(ctx, "ghost"))
        self.assertEqual(plan.find(ctx, task.id).state, plan.NEXT)
        self.assertIsNone(
            self.decision_of(
                self.hook(
                    "pre-tool",
                    session_id="alive",
                    hook_event_name="PreToolUse",
                    tool_name="Edit",
                    tool_input={
                        "file_path": str(self.repo / "src" / "billing.js"),
                        "new_string": "z",
                    },
                )
            )
        )

    def test_work_in_two_worktrees_merges_cleanly(self):
        """The property everything else rests on, exercised through the real files."""
        self.build_app()
        ctx = self.ctx()
        plan.add(ctx, "on main")
        self.commit("plan on main")

        git(["checkout", "-qb", "feature"], self.repo)
        plan.add(ctx, "on feature")
        self.commit("plan on feature")

        git(["checkout", "-q", "main"], self.repo)
        git(["merge", "-q", "--no-edit", "feature"], self.repo)

        titles = {t.title for t in plan.load_all(ctx)}
        self.assertEqual(titles, {"on main", "on feature"})


class TestMemoryStaysHonest(Scenario):
    def test_a_claim_is_suppressed_once_its_subject_is_rewritten(self):
        self.build_app()
        ctx = self.ctx()
        board.add_open_item(
            ctx, "item-1", "billing rounds down, check before invoicing",
            ctx.branch, "s1", ["src/billing.js"],
        )
        me = self.session_record("s1")
        self.assertIn("billing rounds down", board.render(ctx, me, [], 0))

        self.write("src/billing.js", "export function total() { return 0 }\n")
        rendered = board.render(ctx, me, [], 0)
        self.assertNotIn("billing rounds down", rendered)
        self.assertIn("stale (suppressed)", rendered)

    def test_a_renamed_type_breaks_its_anchor_loudly(self):
        self.build_app()
        self.hook("setup", session_id="s1", hook_event_name="Setup")
        self.assertEqual(
            [p for p in knowledge.validate(self.ctx()) if "no longer resolves" in str(p)], []
        )

        self.write(
            "src/models.js",
            "export class Bill { constructor(id) { this.id = id } }\n"
            "export class Client { constructor(email) { this.email = email } }\n",
        )
        problems = [str(p) for p in knowledge.validate(self.ctx())]
        self.assertTrue(any("no longer resolves" in p for p in problems), problems)

    def test_a_checkpoint_survives_the_window_collapsing(self):
        self.build_app()
        self.hook("session-start", session_id="s1", hook_event_name="SessionStart")
        self.write("src/billing.js", "export function total() { return 42 }\n")

        proc = self.hook(
            "checkpoint", session_id="s1", hook_event_name="PreCompact", trigger="auto"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        saved = list((self.repo / ".claude" / "claude-bestpractice" / "checkpoints").glob("*.md"))
        self.assertEqual(len(saved), 1)
        text = saved[0].read_text()
        self.assertIn("src/billing.js @ ", text)
        self.assertIn("baseline_commit:", text)

    def test_reindex_rebuilds_everything_derived(self):
        """Tier B must always be safe to delete, and that path must be exercised."""
        self.build_app()
        self.hook("session-start", session_id="s1", hook_event_name="SessionStart")
        plan.add(self.ctx(), "durable task")

        proc = self.cli("claude-bp-reindex")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        # Tier A survived; only derived state was dropped.
        self.assertEqual(len(plan.load_all(self.ctx())), 1)
        self.assertEqual(
            self.hook("session-start", session_id="s2", hook_event_name="SessionStart").returncode, 0
        )


class TestConflictTakeover(Scenario):
    def test_a_contesting_hook_is_detected_and_quarantined(self):
        self.build_app()
        self.write(
            ".claude/settings.json",
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {"hooks": [{"type": "command", "command": "/opt/other-tool/stop-gate"}]}
                        ],
                        "PostToolUse": [
                            {"hooks": [{"type": "command", "command": "/opt/other-tool/formatter"}]}
                        ],
                    }
                },
                indent=2,
            ),
        )
        ctx = self.ctx()
        found = conflicts.detect(ctx)
        contested = [c for c in found if c.action == "quarantine"]
        self.assertTrue(contested, "a competing Stop gate was not flagged")

        moved, backups = conflicts.quarantine_loose_hooks(ctx)
        self.assertEqual(moved, 1)
        self.assertTrue(backups)

        data = json.loads((self.repo / ".claude" / "settings.json").read_text())
        self.assertNotIn("Stop", data["hooks"])
        # An uncontested hook is left alone: this is not a land grab.
        self.assertIn("PostToolUse", data["hooks"])
        self.assertIn("Stop", data[conflicts.QUARANTINE_KEY])

    def test_quarantine_is_reversible(self):
        """An override nobody can undo is a trap, not a feature."""
        self.build_app()
        self.write(
            ".claude/settings.json",
            json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/x/gate"}]}]}}),
        )
        ctx = self.ctx()
        conflicts.quarantine_loose_hooks(ctx)
        self.assertEqual(conflicts.restore_quarantined(ctx), 1)

        data = json.loads((self.repo / ".claude" / "settings.json").read_text())
        self.assertIn("Stop", data["hooks"])
        self.assertNotIn(conflicts.QUARANTINE_KEY, data)

    def test_our_own_hooks_are_never_quarantined(self):
        self.build_app()
        self.write(
            ".claude/settings.json",
            json.dumps(
                {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/x/claude-bestpractice/bin/evidence-gate"}]}]}}
            ),
        )
        moved, _ = conflicts.quarantine_loose_hooks(self.ctx())
        self.assertEqual(moved, 0)

    def test_nothing_changes_without_being_asked(self):
        self.build_app()
        original = json.dumps(
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/x/gate"}]}]}}, indent=2
        )
        self.write(".claude/settings.json", original)

        proc = self.cli("claude-bp", "adopt", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("dry run", proc.stdout)
        self.assertEqual((self.repo / ".claude" / "settings.json").read_text(), original)


class TestStageEscalation(Scenario):
    def test_rigor_arrives_with_the_product_and_never_leaves(self):
        self.build_app()
        self.hook("session-start", session_id="s1", hook_event_name="SessionStart")

        migration = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.repo / "migrations" / "0002.sql"),
                "content": "DROP TABLE clients;",
            },
        }
        # A prototype is not given ceremony it has not earned.
        self.assertIsNone(self.decision_of(self.hook("pre-tool", session_id="s1", **migration)))

        # A users table is the documented signal that people's data now exists.
        self.write("migrations/0001_init.sql", "CREATE TABLE users (id serial primary key);")
        self.commit("users")
        self.assertEqual(self.decision_of(self.hook("pre-tool", session_id="s1", **migration)), "deny")

        # And the ratchet does not release when the signal is removed.
        (self.repo / "migrations" / "0001_init.sql").unlink()
        self.commit("remove migration")
        self.assertEqual(self.decision_of(self.hook("pre-tool", session_id="s1", **migration)), "deny")


if __name__ == "__main__":
    unittest.main()
