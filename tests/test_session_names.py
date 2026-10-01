"""Issue #253: the plugin named every session by its id, and `SendMessage` takes a name.

Three live chats in one repository: `ListAgents` said `fuddy-8b` and `fuddy-ae`, the board
said `80672d34` and `f98489fa`, a card's owner was `80672d34-2932-…-a2f971ed`, and the only
way to tell which chat to write to about a card or a pull request was matching files under
`~/.claude/sessions/` by hand. On the same board, two sessions nobody was in, one chat
listed in a tree it had only `cd`-ed into to read, and a `task:` line that was the founder's
last message, typos and all, rather than the card the session had taken.

`claude agents --json` is faked here the way `gh` is elsewhere: a `claude` first on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest import mock

from helpers import BIN, RepoCase, session_record_for, sid

from claude_bestpractice import board, gitpolicy, inbox, plan, pullrequest, sessions
from claude_bestpractice.gitctx import resolve

from test_gates import JUNIT_PASS, GateCase
from test_one_session_two_trees import DSN, MovedSession


class ClaudeCodeSays(RepoCase):
    """A `claude` first on PATH that answers `agents --json` with what the test says."""

    def setUp(self) -> None:
        super().setUp()
        self.stubs = self.tmp / "claude-stub"
        self.stubs.mkdir()
        (self.stubs / "claude").write_text("#!/bin/sh\nprintf '%s' \"$FAKE_AGENTS\"\n")
        (self.stubs / "claude").chmod(0o755)

    def claude_code_lists(self, *entries: dict) -> None:
        patched = mock.patch.dict(os.environ, {
            "PATH": f"{self.stubs}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_AGENTS": json.dumps(list(entries)),
        })
        patched.start()
        self.addCleanup(patched.stop)
        asking = mock.patch.dict(os.environ)
        asking.start()
        os.environ.pop(sessions.NO_AGENTS_ENV, None)
        self.addCleanup(asking.stop)

    def registered(self, harness: str, **fields) -> sessions.SessionRecord:
        rec = session_record_for(self.ctx(), sid(self.repo, harness))
        for key, value in fields.items():
            setattr(rec, key, value)
        sessions.register(self.ctx(), rec)
        return sessions.get(self.ctx(), rec.session_id)


class TestClaudeCodeIsAsked(ClaudeCodeSays):
    def test_its_sessions_come_back_by_id(self):
        self.claude_code_lists({"sessionId": "s1", "name": "fuddy-8b", "pid": 101})
        self.assertEqual("fuddy-8b", sessions.running()["s1"]["name"])

    def test_the_suite_switch_asks_nothing(self):
        self.claude_code_lists({"sessionId": "s1", "name": "fuddy-8b", "pid": 101})
        with mock.patch.dict(os.environ, {sessions.NO_AGENTS_ENV: "1"}):
            self.assertEqual({}, sessions.running())

    def test_an_answer_it_cannot_read_is_no_answer(self):
        self.claude_code_lists()
        os.environ["FAKE_AGENTS"] = "not json"
        self.assertEqual({}, sessions.running())


class TestEverySessionIsNamed(ClaudeCodeSays):
    def test_a_record_learns_the_name_its_session_answers_to(self):
        rec = self.registered("s1")
        self.claude_code_lists({"sessionId": "s1", "name": "fuddy-8b", "pid": rec.pid})
        sessions.learn_names(self.ctx(), sessions.running())
        named = sessions.get(self.ctx(), rec.session_id)
        self.assertEqual("fuddy-8b", named.name)
        self.assertEqual(f"fuddy-8b ({rec.session_id[:8]})", sessions.label(named))

    def test_naming_a_sibling_does_not_make_it_look_alive(self):
        rec = self.registered("s1", heartbeat_at=1.0)
        stamped = sessions.get(self.ctx(), rec.session_id).heartbeat_at
        sessions.learn_names(self.ctx(), {"s1": {"name": "fuddy-8b"}})
        self.assertEqual(stamped, sessions.get(self.ctx(), rec.session_id).heartbeat_at)

    def test_a_rename_is_learned_the_next_time(self):
        rec = self.registered("s1", name="fuddy-8b")
        sessions.learn_names(self.ctx(), {"s1": {"name": "billing-export"}})
        self.assertEqual("billing-export", sessions.get(self.ctx(), rec.session_id).name)

    def test_an_owner_is_named_wherever_only_its_id_is_kept(self):
        rec = self.registered("s1", name="fuddy-8b")
        self.assertEqual(f"fuddy-8b ({rec.session_id[:8]})", sessions.called(self.ctx(), rec.session_id))
        self.assertEqual("gone-but", sessions.called(self.ctx(), "gone-but-named-by-id"))


class TestAConversationItsProcessLeftIsNotLive(ClaudeCodeSays):
    """`/clear` and `/resume` move a running CLI to a new session id, and the record left
    under the old one stayed live for as long as the CLI ran: `debfe145` and `ef119b43`."""

    def test_the_process_running_another_conversation_retires_it(self):
        old = self.registered("s1")
        live = {"s2": {"sessionId": "s2", "pid": old.pid}}
        self.assertTrue(sessions.superseded(old, live))
        self.assertNotIn(old.session_id,
                         [r.session_id for r in sessions.live_sessions(self.ctx(), live=live)])

    def test_the_reaper_releases_it(self):
        old = self.registered("s1")
        reaped = sessions.reap(self.ctx(), live={"s2": {"sessionId": "s2", "pid": old.pid}})
        self.assertEqual([old.session_id], [r.session_id for r in reaped])
        self.assertIsNone(sessions.get(self.ctx(), old.session_id))

    def test_still_listed_under_its_own_id_it_is_alive(self):
        old = self.registered("s1")
        self.assertFalse(sessions.superseded(old, {"s1": {"pid": old.pid}, "s2": {"pid": old.pid}}))

    def test_nothing_said_about_its_process_is_no_evidence(self):
        old = self.registered("s1")
        self.assertFalse(sessions.superseded(old, {"s2": {"pid": old.pid + 1}}))
        self.assertFalse(sessions.superseded(old, {}))

    def test_a_pid_that_is_not_the_cli_proves_nothing(self):
        old = self.registered("s1", pid_trust=sessions.PID_TRUST_PARENT)
        self.assertFalse(sessions.superseded(old, {"s2": {"pid": old.pid}}))

    def test_a_pid_read_in_another_namespace_proves_nothing(self):
        """The same number there is another process, or none."""
        old = self.registered("s1")
        elsewhere = sessions.touch(self.ctx(), old.session_id, pid_identity="another-namespace")
        self.assertFalse(sessions.superseded(elsewhere, {"s2": {"pid": old.pid}}))


class TestOneRowPerSession(ClaudeCodeSays):
    """A read-only `cd` into another tree registers the session there too, and it was listed
    in that tree as if working in it."""

    def test_the_tree_it_wrote_in_is_the_one_shown(self):
        tree = self.add_worktree("neighbour")
        working = self.registered("s1", last_touched=["src/app.py"])
        reading = session_record_for(resolve(tree), sid(tree, "s1"))
        reading.heartbeat_at = working.heartbeat_at + 60
        sessions.register(resolve(tree), reading)

        shown = sessions.one_per_session(sessions.load_all(self.ctx()))
        self.assertEqual([working.session_id], [r.session_id for r in shown])

    def test_before_any_write_it_is_the_tree_it_started_in(self):
        tree = self.add_worktree("neighbour")
        started = self.registered("s1")
        reading = session_record_for(resolve(tree), sid(tree, "s1"))
        reading.started_at = started.started_at + 60
        reading.heartbeat_at = started.heartbeat_at + 60
        sessions.register(resolve(tree), reading)

        shown = sessions.one_per_session(sessions.load_all(self.ctx()))
        self.assertEqual([started.session_id], [r.session_id for r in shown])

    def test_two_processes_are_two_sessions(self):
        first = self.registered("s1")
        second = session_record_for(self.ctx(), sid(self.repo, "s2"), pid=first.pid + 100000)
        sessions.register(self.ctx(), second)
        self.assertEqual(2, len(sessions.one_per_session(sessions.load_all(self.ctx()))))


class TestTheBoardNamesThem(ClaudeCodeSays):
    def test_a_sibling_is_listed_by_name_with_the_card_it_holds(self):
        me = self.registered("me")
        other = self.registered("s1", name="fuddy-8b",
                                task_statement="тебя можно закрывать?")
        card = plan.add(self.ctx(), "Billing CSV export", paths=["src/billing.py"],
                        done_when="stated")
        plan.claim(self.ctx(), card.id, other.session_id, "feat/billing")

        body = board.render(self.ctx(), me, [other], reaped=0)
        self.assertIn(f"fuddy-8b ({other.session_id[:8]}) on", body)
        self.assertIn(f"task: {card.id} Billing CSV export", body)
        self.assertNotIn("тебя можно закрывать", body)

    def test_a_pull_request_says_which_chat_opened_it(self):
        other = self.registered("s1", name="fuddy-8b")
        pullrequest.opened(self.ctx(), "feat/billing", "main", other.session_id, number=812)
        self.assertIn(f"#812 on feat/billing (ready to merge), by fuddy-8b ({other.session_id[:8]})",
                      pullrequest.line(self.ctx()))


class TestASessionStartAsksOnce(ClaudeCodeSays, GateCase):
    def test_the_board_it_injects_names_the_sibling(self):
        self.start("s1")
        sibling = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.claude_code_lists({"sessionId": "s1", "name": "fuddy-8b", "pid": sibling.pid},
                               {"sessionId": "s2", "name": "fuddy-ae", "pid": sibling.pid + 1})
        body = json.loads(self.start("s2").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"fuddy-8b ({sibling.session_id[:8]})", body)
        self.assertEqual("fuddy-ae", sessions.get(self.ctx(), sid(self.repo, "s2")).name)

    def test_status_names_sessions_and_owners(self):
        self.start("s1")
        sibling = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.claude_code_lists({"sessionId": "s1", "name": "fuddy-8b", "pid": sibling.pid})
        card = plan.add(self.ctx(), "Billing CSV export", paths=["src/billing.py"],
                        done_when="stated")
        plan.claim(self.ctx(), card.id, sibling.session_id, "main")

        status = subprocess.run([sys.executable, str(BIN / "claude-bp"), "status"],
                                capture_output=True, text=True, cwd=str(self.repo), timeout=120)
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertIn(f"fuddy-8b ({sibling.session_id[:8]})", status.stdout)
        self.assertIn(f"Billing CSV export [fuddy-8b ({sibling.session_id[:8]})]", status.stdout)


class TestTheOneToAskIsNamed(ClaudeCodeSays, GateCase):
    """Wherever the plugin sends a session to another — a file or a card the other holds, a
    question it put or answered, the tree it works in — the other is named the way
    `SendMessage` addresses it, which is the moment the name is needed."""

    def named(self, harness: str, name: str) -> sessions.SessionRecord:
        self.start(harness)
        sessions.learn_names(self.ctx(), {harness: {"name": name}})
        return sessions.get(self.ctx(), sid(self.repo, harness))

    def edit(self, harness: str):
        return self.gate("pre-tool", {
            "session_id": harness, "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo / "src/shared.py"), "new_string": "y = 2"},
        })

    def test_the_session_holding_the_file(self):
        holder = self.named("alpha", "fuddy-8b")
        blocked = self.named("beta", "fuddy-ae")
        self.edit("alpha")
        refused = json.loads(self.edit("beta").stdout)["hookSpecificOutput"]
        self.assertEqual("deny", refused["permissionDecision"])
        self.assertIn(f"session fuddy-8b ({holder.session_id[:8]}) on branch",
                      refused["permissionDecisionReason"])
        self.assertIn(f"session fuddy-ae ({blocked.session_id[:8]}) is blocked on src/shared.py",
                      [ask.get("text") for ask in inbox.open_asks(self.ctx(), holder.session_id)][0])

    def test_two_trees_on_one_file_are_told_who_the_other_is(self):
        tree = self.add_worktree("neighbour")
        here = self.named("alpha", "fuddy-8b")
        self.gate("session-start", {"session_id": "beta", "hook_event_name": "SessionStart",
                                    "source": "startup", "cwd": str(tree)})
        sessions.learn_names(self.ctx(), {"beta": {"name": "fuddy-ae"}})
        there = sessions.get(self.ctx(), sid(tree, "beta"))
        self.edit("alpha")
        self.gate("pre-tool", {
            "session_id": "beta", "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "cwd": str(tree),
            "tool_input": {"file_path": str(tree / "src/shared.py"), "new_string": "y = 2"},
        })
        told_here = [note.get("text") for note in inbox.pending(self.ctx(), here.session_id)]
        told_there = [note.get("text") for note in inbox.pending(self.ctx(), there.session_id)]
        self.assertTrue(any(text.startswith(
            f"session fuddy-ae ({there.session_id[:8]}) is editing src/shared.py too")
            for text in told_here), told_here)
        self.assertTrue(any(text.startswith(f"session fuddy-8b ({here.session_id[:8]}) on ")
                            for text in told_there), told_there)

    def test_the_session_holding_the_card(self):
        holder = self.named("alpha", "fuddy-8b")
        card = plan.add(self.ctx(), "Billing CSV export", paths=["src/billing.py"],
                        done_when="stated")
        plan.claim(self.ctx(), card.id, holder.session_id, "feat/billing")
        _, error = plan.claim(self.ctx(), card.id, sid(self.repo, "beta"), "main")
        self.assertIn(f"held by live session fuddy-8b ({holder.session_id[:8]})", error)

    def test_the_session_waiting_on_an_answer(self):
        asker = self.named("alpha", "fuddy-8b")
        self.start("s1")
        self.claim_a_task("s1", "feature.py")
        self.write("feature.py", "x = 1\n")
        self.write("junit.xml", JUNIT_PASS)
        inbox.ask(self.ctx(), sid(self.repo, "s1"), "are you still in schemas.py?",
                  sender=asker.session_id)
        proc = self.stop()
        self.assertEqual(2, proc.returncode)
        self.assertIn(f"fuddy-8b ({asker.session_id[:8]}) asks: are you still in schemas.py?",
                      proc.stderr)

    def test_the_session_that_answered(self):
        answerer = self.named("alpha", "fuddy-8b")
        me = sid(self.repo, "s1")
        inbox.ask(self.ctx(), answerer.session_id, "are you still in schemas.py?", sender=me)
        key = inbox.open_asks(self.ctx(), answerer.session_id)[0]["key"][:12]
        self.assertTrue(inbox.answer(self.ctx(), answerer.session_id, key, "yes, still in it"))
        self.assertIn(f"answer from fuddy-8b ({answerer.session_id[:8]}): yes, still in it",
                      [note.get("text") for note in inbox.pending(self.ctx(), me)])

    def test_the_session_working_in_the_other_tree(self):
        self.named("alpha", "fuddy-8b")
        tree = resolve(self.add_worktree("neighbour"))
        there = session_record_for(tree, sid(tree.worktree_root, "s2"))
        there.name = "fuddy-ae"
        sessions.register(tree, there)
        owner = tree.worktree_root.resolve()

        named = f"\n  fuddy-ae ({there.session_id[:8]}) works there.\n"
        self.assertIn(named,
                      gitpolicy.foreign_refusal(owner / "src" / "app.py", owner, self.ctx()))
        self.assertIn(named, gitpolicy.foreign_git_refusal(owner, self.ctx()))

    def test_a_tree_nobody_live_works_in_is_still_somebody_elses(self):
        owner = resolve(self.add_worktree("neighbour")).worktree_root.resolve()
        refused = gitpolicy.foreign_git_refusal(owner, self.ctx())
        self.assertIn(f"another session's worktree ({owner})", refused)
        self.assertNotIn("works there", refused)


class TestTheSessionOnTheSameDatabaseIsNamed(MovedSession):
    def test_the_refusal_names_it(self):
        sibling = self.a_sibling(self.repo, "B")
        sessions.learn_names(resolve(self.repo), {"B": {"name": "fuddy-ae"}})
        (self.repo / ".env").write_text(f"DATABASE_URL={DSN}\n", encoding="utf-8")
        (self.tree / ".env").write_text(f"DATABASE_URL={DSN}\n", encoding="utf-8")
        proc = self.write_in(self.tree)
        self.assertEqual("deny", self.hook_decision(proc))
        self.assertIn(f"session fuddy-ae ({sibling[:8]}) is already using the database",
                      self.hook_reason(proc))
