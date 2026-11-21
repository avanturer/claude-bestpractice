"""A write refused for "nothing on the board" names the card that already covers it (#368).

The lead's card named `mobile/src/components/product` and waited in `next`: its `claim` had been
refused for want of a plan, and `| tail -1` showed only the advice. The agent's edits passed while
the chat held an unrelated card, and once that was paused the next one was refused, and sent to
plan the card a founder's reply had just opened. The directory matching was right; the remedy was
not.
"""

from __future__ import annotations

import os
import subprocess
import sys

from helpers import BIN, RepoCase, session_record_for, sid

from claude_bestpractice import plan, sessions
from claude_bestpractice.gitctx import resolve

THIS_PROCESS = os.getpid()


class CoverCase(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("src/product/Old.tsx", "export const a = 1\n")
        self.write("docs/x.md", "x\n")
        self.commit("history")
        self.hook("session-start", self.repo, hook_event_name="SessionStart")
        # What the founder asked for, which is what makes a write owe a card at all.
        self.hook("prompt-capture", self.repo, hook_event_name="UserPromptSubmit",
                  prompt="Shorten the state notes in src/product to a couple of words")
        self.opened = plan.opened_for(self.ctx(), "S")

    def hook(self, gate, cwd, session="S", **event):
        return self.run_hook(gate, {"session_id": session, **event}, cwd=cwd)

    def cli(self, *args, session="S"):
        env = {**os.environ, "CLAUDE_CODE_SESSION_ID": session}
        return subprocess.run([sys.executable, str(BIN / "claude-bp-plan"), *args],
                              capture_output=True, text=True, cwd=str(self.repo), env=env,
                              timeout=120)

    def pin(self, tree) -> None:
        ctx = resolve(tree)
        identity = sid(tree, "S")
        record = sessions.get(ctx, identity) or session_record_for(ctx, identity, THIS_PROCESS)
        record.pid, record.pid_trust = THIS_PROCESS, sessions.PID_TRUST_OWNER
        sessions.register(ctx, record)

    def write_in(self, tree, rel, agent=True):
        event = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                 "tool_input": {"file_path": str(tree / rel), "content": "x = 1\n"}}
        if agent:
            event.update(agent_id="a6d0b4af01774a2d0", agent_type="general-purpose")
        return self.hook("pre-tool", tree, **event)

    def the_incident(self, founder_says: str = ""):
        """1365 held and then paused, 1374 naming the directory left in `next`."""
        other = self.opened
        self.assertEqual(0, self.cli("update", other.id, "--paths", "docs/x.md",
                                     "--done-when", "stated").returncode)
        self.assertEqual(0, self.cli("claim", other.id).returncode)
        added = self.cli("add", "short notes everywhere", "--paths", "src/product",
                         "--done-when", "stated")
        self.assertEqual(0, added.returncode, added.stderr)
        covering = added.stdout.split()[0]
        tree = self.add_worktree("repo-short-notes-2")
        self.pin(self.repo)
        self.pin(tree)
        self.assertNotEqual("deny", self.hook_decision(
            self.write_in(tree, "src/product/ProductDetailStates.tsx")))
        self.assertEqual(0, self.cli("pause", other.id, "--blocker", "a quiet machine").returncode)
        if founder_says:
            self.hook("prompt-capture", self.repo, hook_event_name="UserPromptSubmit",
                      prompt=founder_says)
        return tree, covering, other


class TestTheCardOnTheBoardIsNamed(CoverCase):
    def test_the_refusal_names_claim_of_the_card_naming_the_directory(self):
        tree, covering, _ = self.the_incident(
            "Set up the APNs key and the Firebase project for push notifications yourself")
        proc = self.write_in(tree, "src/product/CosmeticNoRatingState.tsx")
        self.assertEqual("deny", self.hook_decision(proc))
        why = self.hook_reason(proc)
        self.assertIn(f"claude-bp-plan claim {covering}", why)
        self.assertNotIn("update", why.split("--")[0])

    def test_the_command_it_names_runs_and_the_write_then_passes(self):
        tree, covering, _ = self.the_incident("you can do it all yourself, you have every right")
        self.assertEqual("deny", self.hook_decision(
            self.write_in(tree, "src/product/CosmeticNoRatingState.tsx")))
        self.assertEqual(0, self.cli("claim", covering).returncode)
        self.assertNotEqual("deny", self.hook_decision(
            self.write_in(tree, "src/product/CosmeticNoRatingState.tsx")))

    def test_a_paused_card_naming_it_is_named_too(self):
        tree, _covering, other = self.the_incident("you can do it all yourself, you have every right")
        proc = self.write_in(tree, "docs/x.md")
        self.assertIn(f"claude-bp-plan claim {other.id}", self.hook_reason(proc))

    def test_a_card_claim_would_refuse_is_not_offered(self):
        """Named with no condition, `claim` refuses it: a refusal names what runs (decision 0020)."""
        plan.add(self.ctx(), "no condition yet", paths=["src/product"])
        self.assertIsNone(plan.covering(self.ctx(), sid(self.repo, "S"), ["src/product/A.tsx"]))

    def test_a_card_naming_another_directory_is_not_offered(self):
        plan.add(self.ctx(), "elsewhere", paths=["src/productivity"], done_when="stated")
        self.assertIsNone(plan.covering(self.ctx(), sid(self.repo, "S"), ["src/product/A.tsx"]))


class TestAPlannedCardIsNotShrunk(CoverCase):
    def test_the_update_it_names_keeps_what_the_card_named(self):
        opened = self.opened
        self.assertIsNotNone(opened)
        self.assertEqual(0, self.cli("update", opened.id, "--title", "short notes",
                                     "--paths", "docs/x.md", "--done-when", "stated").returncode)
        proc = self.write_in(self.repo, "lib/new.py", agent=False)
        self.assertEqual("deny", self.hook_decision(proc))
        why = self.hook_reason(proc)
        self.assertIn(f"claude-bp-plan update {opened.id} --paths docs/x.md,lib/new.py", why)
        self.assertNotIn("--title", why.split("--\n")[0])
        # And it runs as printed: the card then names both, its title as it was.
        self.assertEqual(0, self.cli("update", opened.id, "--paths", "docs/x.md,lib/new.py").returncode)
        card = plan.find(self.ctx(), opened.id)
        self.assertEqual(["docs/x.md", "lib/new.py"], card.paths)
        self.assertEqual("short notes", card.title)


class TestARefusedClaimSaysSoLast(CoverCase):
    def test_the_last_line_of_a_refused_claim_says_it_was_not_claimed(self):
        card = plan.add(self.ctx(), "no plan yet")
        proc = self.cli("claim", card.id)
        self.assertEqual(1, proc.returncode)
        self.assertEqual(f"not claimed: {card.id}", proc.stderr.strip().splitlines()[-1])


class TestTheBriefNamesNoCardAsTheAgentsOfSeveral(CoverCase):
    def brief(self) -> str:
        proc = self.hook("subagent-brief", self.repo, hook_event_name="SubagentStart",
                         agent_id="a1", agent_type="general-purpose")
        return proc.stdout

    def test_one_card_is_the_one_it_works_under(self):
        self.claim_a_task("S", "src/product")
        self.assertIn("works under card", self.brief())

    def test_of_two_neither_is_said_to_be_its_own(self):
        self.claim_a_task("S", "src/product")
        self.claim_a_task("S", "docs/x.md")
        brief = self.brief()
        self.assertNotIn("works under card", brief)
        self.assertIn("The session that started this agent holds card", brief)
