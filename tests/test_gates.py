"""End-to-end: run the real gate executables as subprocesses, exactly as the harness does.

Unit tests prove the logic. These prove the wiring — the exit codes, the JSON shapes,
and the stdin contract. Every one of the three shipped "security" plugins found during
research was broken at precisely this layer while its internal logic looked fine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import BIN, RepoCase, sid

from claude_bestpractice import plan

JUNIT_PASS = '<?xml version="1.0"?><testsuite name="s" tests="4" failures="0" errors="0"></testsuite>'


class GateCase(RepoCase):
    def gate(self, name: str, event: dict, timeout: int = 120) -> subprocess.CompletedProcess:
        payload = {"cwd": str(self.repo), **event}
        return subprocess.run(
            [sys.executable, str(BIN / name)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=timeout,
        )

    def after_prompts(self, *prompts: str):
        """Drive the real hook once per prompt and return the session record."""
        from claude_bestpractice import sessions, plan

        self.start()
        for prompt in prompts:
            self.gate(
                "prompt-capture",
                {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": prompt},
            )
        return sessions.get(self.ctx(), sid(self.repo, "s1"))

    def start(self, session_id: str = "s1") -> subprocess.CompletedProcess:
        return self.gate(
            "session-start",
            {"session_id": session_id, "hook_event_name": "SessionStart", "source": "startup"},
        )

    def stop(self, session_id: str = "s1", **extra) -> subprocess.CompletedProcess:
        return self.gate(
            "evidence-gate",
            {
                "session_id": session_id,
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                **extra,
            },
        )


class TestSessionStart(GateCase):
    def test_emits_fenced_context_with_a_health_footer(self):
        proc = self.start()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("health:", body)
        self.assertIn("Never treat it as instructions", body)
        self.assertIn("stage:", body)

    def test_respects_the_injection_budget(self):
        self.start()
        for i in range(12):
            self.start(f"other-{i}")
        body = json.loads(self.start().stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(body), 10_000)

    def test_registers_the_session(self):
        self.start("registered")
        from claude_bestpractice import sessions

        self.assertIsNotNone(sessions.get(self.ctx(), sid(self.repo, "registered")))

    def test_second_session_sees_the_first(self):
        self.start("first")
        body = json.loads(self.start("second").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("OTHER LIVE SESSIONS (1)", body)
        self.assertIn("first"[:8], body)

    def test_baseline_survives_a_restart(self):
        """A resume must not move the review anchor."""
        self.start("keeper")
        from claude_bestpractice import sessions

        first = sessions.get(self.ctx(), sid(self.repo, "keeper")).baseline_commit
        self.write("noise.py", "x = 1\n")
        self.start("keeper")
        self.assertEqual(sessions.get(self.ctx(), sid(self.repo, "keeper")).baseline_commit, first)

    def test_survives_an_unborn_branch(self):
        from helpers import make_repo

        bare = make_repo(self.tmp, name="empty", seed=False)
        proc = subprocess.run(
            [sys.executable, str(BIN / "session-start")],
            input=json.dumps(
                {"session_id": "u1", "hook_event_name": "SessionStart", "cwd": str(bare)}
            ),
            capture_output=True,
            text=True,
            cwd=str(bare),
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestPromptCapture(GateCase):
    def test_captures_the_first_prompt_verbatim(self):
        self.start()
        self.write("src/auth.py", "x = 1\n")
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "Fix the login bug in src/auth.py",
            },
        )
        from claude_bestpractice import sessions

        rec = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(rec.task_statement, "Fix the login bug in src/auth.py")
        self.assertIn("src/auth.py", rec.task_paths)

    def test_injects_nothing(self):
        """Per-turn injection is the most expensive mistake available to this plugin."""
        self.start()
        proc = self.gate(
            "prompt-capture",
            {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "do a thing"},
        )
        self.assertNotIn("additionalContext", proc.stdout)


    def test_the_task_follows_the_founder(self):
        """Reversed deliberately: this used to freeze on the first prompt and never move.

        Reported from a real session — the gate was still measuring against "посмотри
        заново бд я перезапустил д" many turns after new instructions, and quoted it back
        in every refusal. An anchor nobody can move is not an anchor, it is a stale claim.
        """
        rec = self.after_prompts("original task", "now do something else")
        self.assertEqual(rec.task_statement, "now do something else")

    def test_paths_accumulate_where_the_statement_replaces(self):
        """Dropping the first instruction's files would make them drift on the second."""
        self.write("first.py", "x = 1\n")
        self.write("second.py", "y = 2\n")
        rec = self.after_prompts("edit first.py", "now also edit second.py")
        self.assertEqual(rec.task_paths, ["first.py", "second.py"])

    def test_what_the_ide_opened_is_not_the_task(self):
        """The whole reported defect, end to end through the real hook.

        Claude Code injects `<ide_opened_file>` into the prompt, and it carries a path —
        the one thing this hook mines a prompt for. On a real machine it named
        `/tmp/readonly/Bash tool output (aeqikl)`, that became the entire task scope, and
        every genuine project file was therefore drift. Eight consecutive blocks on
        correct work, with the escalation counter recording an unverified finish twice.

        The block says of itself that it "may or may not be related to the current task",
        which is a definition of not being it.
        """
        from claude_bestpractice import evidence, sessions

        self.start()
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": (
                    "<ide_opened_file>The user opened the file /tmp/readonly/Bash tool "
                    "output (aeqikl) in the IDE. This may or may not be related to the "
                    "current task.</ide_opened_file>"
                ),
            },
        )
        rec = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(rec.task_paths, [], "the IDE's path became the task scope")
        # Empty scope disables the check rather than blocking everything, which is the
        # safety valve the injected path was walking straight past.
        self.assertEqual(evidence.scope_drift(["src/real.py"], rec.task_paths, []), [])

    def test_an_ide_block_naming_a_real_file_is_still_not_the_task(self):
        """Containment alone would not catch this one, so it is asserted separately.

        A file the founder happened to open in their editor is inside the repository and
        passes every "is this a real path" test there is. It would silently become the
        task scope, and then every other file in the change is drift.
        """
        from claude_bestpractice import sessions

        self.write("opened.py", "x = 1\n")
        self.start()
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "<ide_opened_file>The user opened the file opened.py in the IDE."
                          "</ide_opened_file>fix other.py",
            },
        )
        rec = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertNotIn("opened.py", rec.task_paths)

    def test_a_path_outside_the_worktree_is_never_a_task_path(self):
        """`root / "/tmp/x"` is `/tmp/x` — an absolute token discards the root entirely.

        The directory fallback then accepted anything whose parent existed anywhere on
        the machine, up to and including `/`, which is why the closing tag itself
        (`/ide_opened_file`) was kept with no help from the filesystem at all. These
        paths are compared against repository-relative names, so one that is not in the
        repository cannot match anything and turns the check into "all of it is drift".
        """
        from claude_bestpractice import sessions

        self.start()
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "compare against /etc/hosts and /ide_opened_file please",
            },
        )
        rec = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(rec.task_paths, [])

    def test_xml_the_founder_typed_is_left_alone(self):
        """Stripping is by name, not by angle bracket: pasted XML is asking to be read."""
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("pc", str(BIN / "prompt-capture"))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader("pc", loader))
        loader.exec_module(module)

        text = "<config><name>x</name></config> fix README.md"
        self.assertEqual(module.strip_envelopes(text), text)

    def test_prose_mentions_do_not_become_task_paths(self):
        self.start()
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "make it faster, e.g. by caching things.",
            },
        )
        from claude_bestpractice import sessions

        self.assertEqual(sessions.get(self.ctx(), sid(self.repo, "s1")).task_paths, [])


class TestPreTool(GateCase):
    def decision(self, proc: subprocess.CompletedProcess) -> str | None:
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def assertNotRefused(self, proc, message: str = "") -> None:
        """The gate did not refuse this — silently, or by vouching for it.

        Three answers, not two. `assertIsNone` was the right test while the only
        alternative to a refusal was silence; since the plugin also vouches for work it
        ordered or already governs (#99, #102), reading silence as the only shape of
        "not refused" makes an approval indistinguishable from a denial.
        """
        self.assertNotEqual("deny", self.decision(proc), message or (proc.stdout + proc.stderr))


    def test_allows_ordinary_work(self):
        self.start()
        proc = self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "a.py"), "content": "x = 1\n"},
            },
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotRefused(proc)

    def test_denies_a_credential_write(self):
        self.start()
        proc = self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(self.repo / "cfg.py"),
                    "content": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n',
                },
            },
        )
        self.assertEqual(self.decision(proc), "deny")
        self.assertFalse((self.repo / "cfg.py").exists())

    def test_allows_an_env_var_reference(self):
        """The fix the gate recommends must not itself be blocked."""
        self.start()
        proc = self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(self.repo / "cfg.py"),
                    "content": 'API_KEY = os.environ["API_KEY"]\n',
                },
            },
        )
        self.assertNotRefused(proc)

    def bash_event(self, command: str, session_id: str = "s1") -> dict:
        return {
            "session_id": session_id,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }

    def test_denies_a_repeated_signature(self):
        """The identical command back to back with nothing in between is a stuck loop."""
        self.start()
        event = self.bash_event("npm test")
        decisions = [self.decision(self.gate("pre-tool", event)) for _ in range(6)]
        self.assertIn("deny", decisions)

    def test_the_ordinary_fix_loop_is_never_denied(self):
        """Run the suite, fix a thing, run it again. That is the work, not a loop.

        The counter used to be a session lifetime total, so the fourth `npm test` of a
        session was refused for the rest of it — and `npm test` is the command the Stop
        gate orders the agent to run. The two gates deadlocked: finish refused for want
        of a test run, test run refused for having been run before, nothing clearing
        either. This is the single most common shape of an agent's turn.
        """
        self.start()
        for round_number in range(1, 6):
            suite = self.decision(self.gate("pre-tool", self.bash_event("npm test")))
            self.assertNotEqual("deny", suite, f"the suite was refused on round {round_number}")
            self.gate("pre-tool", self.bash_event(f"grep -n thing_{round_number} src.py"))

    def test_doing_anything_else_clears_the_streak(self):
        """A count with no way down is a ratchet, and this one is not meant to be."""
        self.start()
        event = self.bash_event("npm test")
        decisions = [self.decision(self.gate("pre-tool", event)) for _ in range(6)]
        self.assertIn("deny", decisions, "precondition: the streak must trip first")

        self.gate("pre-tool", self.bash_event("git status"))
        self.assertNotEqual(
            "deny",
            self.decision(self.gate("pre-tool", event)),
            "still refused after doing something else — the count never comes down",
        )

    def test_a_registry_written_beside_the_ledger_is_refused_at_write_time(self):
        """End to end: the check that only ran at SessionStart now fires on the write.

        The duplicate in the report was wired into three entry points and committed twice
        before anyone saw it, because nothing spoke while it was still one file (#103).
        """
        from claude_bestpractice import plan

        self.start()
        plan.add(self.ctx(), "a task the ledger already holds")
        proc = self.gate("pre-tool", {
            "session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.repo / "docs/TODO.md"),
                "content": "# TODO\n\n- [ ] recheck the limit\n- [ ] backfill the skus\n",
            },
        })
        self.assertEqual("deny", self.decision(proc), proc.stdout + proc.stderr)

    def test_fails_closed_on_garbage(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "pre-tool")],
            input="not json",
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 2)

    def edit_event(self, session_id: str, relpath: str) -> dict:
        return {
            "session_id": session_id,
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo / relpath), "new_string": "y = 2"},
        }

    def test_denies_a_file_another_live_session_is_editing(self):
        """A silent overwrite is worse than a merge conflict: neither side finds out."""
        self.start("alpha")
        self.start("beta")
        self.gate("pre-tool", self.edit_event("alpha", "src/shared.py"))

        proc = self.gate("pre-tool", self.edit_event("beta", "src/shared.py"))
        self.assertEqual(self.decision(proc), "deny")
        reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("alpha"[:8], reason)

    def test_a_session_may_keep_editing_its_own_file(self):
        self.start("alpha")
        for _ in range(2):
            proc = self.gate("pre-tool", self.edit_event("alpha", "src/mine.py"))
        self.assertNotRefused(proc)

    def test_different_files_do_not_contend(self):
        self.start("alpha")
        self.start("beta")
        self.gate("pre-tool", self.edit_event("alpha", "src/a.py"))
        proc = self.gate("pre-tool", self.edit_event("beta", "src/b.py"))
        self.assertNotRefused(proc)

    def test_lease_is_released_when_the_turn_ends_cleanly(self):
        self.start("alpha")
        self.start("beta")
        self.gate("pre-tool", self.edit_event("alpha", "src/shared.py"))

        from claude_bestpractice import sessions

        self.assertEqual(sessions.leases_held_by(self.ctx(), sid(self.repo, "alpha")), ["src/shared.py"])
        self.stop("alpha")  # nothing changed, so the gate allows and releases
        self.assertEqual(sessions.leases_held_by(self.ctx(), sid(self.repo, "alpha")), [])
        self.assertNotRefused(self.gate("pre-tool", self.edit_event("beta", "src/shared.py")))

    def test_dead_session_lease_does_not_block_forever(self):
        """One crashed session must not poison a path permanently."""
        from claude_bestpractice import sessions

        ctx = self.ctx()
        sessions.register(
            ctx,
            sessions.SessionRecord(
                session_id="ghost",
                pid=999_999_999,
                # The pid is only evidence when it is the CLI's own; see helpers.record.
                pid_trust=sessions.PID_TRUST_OWNER,
                worktree=ctx.worktree_root.as_posix(),
                branch=ctx.branch,
                baseline_commit=ctx.head,
                started_at=time.time(),
                heartbeat_at=time.time(),
            ),
        )
        sessions.acquire_lease(ctx, "ghost", "src/shared.py")

        self.start("live")
        self.assertNotRefused(self.gate("pre-tool", self.edit_event("live", "src/shared.py")))

    def test_tracks_touched_files(self):
        self.start()
        self.gate(
            "pre-tool",
            {
                "session_id": "s1",
                "hook_event_name": "PreToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(self.repo / "src" / "x.py"), "new_string": "y = 2"},
            },
        )
        from claude_bestpractice import sessions

        self.assertIn("src/x.py", sessions.get(self.ctx(), sid(self.repo, "s1")).last_touched)


class TestEvidenceGate(GateCase):
    def test_allows_when_nothing_changed(self):
        self.start()
        self.assertEqual(self.stop().returncode, 0)

    def test_blocks_a_change_with_no_evidence(self):
        self.start()
        self.write("feature.py", "x = 1\n")
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not done yet", proc.stderr)
        self.assertIn("was not read", proc.stderr)

    def test_accepts_fresh_passing_evidence(self):
        self.start()
        self.claim_a_task("s1", "feature.py")
        self.write("feature.py", "x = 1\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        proc = self.stop()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_continuation_still_verifies(self):
        """stop_hook_active bounds the loop, not the check.

        It used to allow unconditionally, which made the gate fire exactly ONCE per user
        turn: block, then every later Stop in that turn passed with no verification, no
        durable record, and leases still held against siblings. The escalation ceiling of
        four was unreachable past one — proven in a live session.
        """
        self.start()
        self.write("app.py", "x = 2\n")
        codes = [self.stop().returncode]
        codes += [self.stop(stop_hook_active=True).returncode for _ in range(4)]
        self.assertEqual(codes[:4], [2, 2, 2, 2], "a continuation skipped verification")
        self.assertEqual(codes[4], 0, "the escalation ceiling never released the turn")

    def test_blocks_scope_drift(self):
        self.start()
        self.write("src/auth.py", "x = 1\n")
        self.commit()
        self.gate(
            "prompt-capture",
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "update src/auth.py only",
            },
        )
        self.write("src/auth.py", "x = 2\n")
        self.write("src/billing.py", "y = 9\n")
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Scope drift", proc.stderr)
        self.assertIn("src/billing.py", proc.stderr)

    def test_gives_up_after_the_escalation_ceiling(self):
        """A gate that wedges the workflow forever gets uninstalled, and then enforces nothing."""
        self.start()
        self.write("feature.py", "x = 1\n")

        from claude_bestpractice import evidence

        codes = [self.stop().returncode for _ in range(evidence.MAX_CONSECUTIVE_BLOCKS + 1)]
        self.assertEqual(codes[: evidence.MAX_CONSECUTIVE_BLOCKS], [2] * evidence.MAX_CONSECUTIVE_BLOCKS)
        self.assertEqual(codes[-1], 0)

        from claude_bestpractice import store

        markers = store.read_jsonl(store.tier_b(self.ctx(), "unverified.jsonl"))
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["session_id"], sid(self.repo, "s1"))

    def test_unverified_finish_surfaces_on_the_next_board(self):
        self.start()
        self.write("feature.py", "x = 1\n")
        from claude_bestpractice import evidence

        for _ in range(evidence.MAX_CONSECUTIVE_BLOCKS + 1):
            self.stop()
        body = json.loads(self.start("next").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("UNVERIFIED", body)

    def test_unlisted_work_is_refused_at_the_finish(self):
        """The ledger was advisory in a product whose premise is that the board tells the
        truth about what the other sessions are doing. A session could rewrite the
        importer for three hours and appear, to every sibling, to be doing nothing.
        """
        self.start()
        self.write("feature.py", "x = 1\n")
        self.write("junit.xml", JUNIT_PASS)

        proc = self.stop()
        self.assertEqual(2, proc.returncode)
        self.assertIn("Nothing on the board", proc.stderr)
        self.assertIn("claude-bp-plan add", proc.stderr, "a refusal must name the way through")

    def test_the_command_the_refusal_names_actually_satisfies_it(self):
        """`claim` stamped `cli-<branch>` as the owner, so the task belonged to somebody
        the registry had never heard of and the demand could not be cleared by its own
        instruction. Identity is (harness id, worktree) in the CLI too."""
        self.start()
        self.write("feature.py", "x = 1\n")
        task = plan.add(self.ctx(), "what this turn is doing", paths=["feature.py"])
        claimed = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-plan"), "claim", task.id],
            capture_output=True, text=True, cwd=str(self.repo), timeout=60,
            env={**os.environ, "CLAUDE_CODE_SESSION_ID": "s1"},
        )
        self.assertEqual(0, claimed.returncode, claimed.stderr)

        self.write("junit.xml", JUNIT_PASS)
        proc = self.stop()
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_counter_resets_after_a_success(self):
        self.start()
        self.claim_a_task("s1", "feature.py")
        self.write("feature.py", "x = 1\n")
        self.assertEqual(self.stop().returncode, 2)
        time.sleep(0.02)
        self.write("junit.xml", JUNIT_PASS)
        self.assertEqual(self.stop().returncode, 0)

        from claude_bestpractice import sessions

        rec = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(rec.tool_signatures.get("_consecutive_blocks"), 0)


class TestCheckpoint(GateCase):
    def test_writes_a_checkpoint_with_provenance(self):
        self.start()
        proc = self.gate(
            "checkpoint",
            {"session_id": "s1", "hook_event_name": "PreCompact", "trigger": "auto"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        files = list((self.repo / ".claude" / "claude-bestpractice" / "checkpoints").glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        self.assertIn("baseline_commit:", text)
        self.assertIn("capture: extractive", text)
        self.assertIn("git_sha:", text)

    def test_scrubs_secrets_from_the_transcript(self):
        self.start()
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "isSidechain": False,
                    "message": {"content": "deploy with AKIAIOSFODNN7EXAMPLE please"},
                }
            )
            + "\n"
        )
        self.gate(
            "checkpoint",
            {
                "session_id": "s1",
                "hook_event_name": "PreCompact",
                "trigger": "auto",
                "transcript_path": str(transcript),
            },
        )
        text = next((self.repo / ".claude" / "claude-bestpractice" / "checkpoints").glob("*.md")).read_text()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", text)
        self.assertIn("[REDACTED]", text)

    def test_survives_an_unreadable_transcript(self):
        """The format is internal and changes between releases. Degrade, never fail."""
        self.start()
        proc = self.gate(
            "checkpoint",
            {
                "session_id": "s1",
                "hook_event_name": "PreCompact",
                "trigger": "auto",
                "transcript_path": "/nonexistent/path.jsonl",
            },
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(list((self.repo / ".claude" / "claude-bestpractice" / "checkpoints").glob("*.md")))

    def test_checkpoints_do_not_collide(self):
        self.start()
        for _ in range(3):
            self.gate(
                "checkpoint",
                {"session_id": "s1", "hook_event_name": "PreCompact", "trigger": "auto"},
            )
            time.sleep(1.01)  # filenames are second-resolution by design
        files = list((self.repo / ".claude" / "claude-bestpractice" / "checkpoints").glob("*.md"))
        self.assertEqual(len(files), 3)


class TestDoctorAndReindex(GateCase):
    def test_doctor_passes_against_a_clean_checkout(self):
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-doctor")],
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("All", proc.stdout)

    def test_reindex_rebuilds_tier_b(self):
        self.start()
        from claude_bestpractice import store

        self.assertTrue(store.tier_b(self.ctx()).exists())
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-reindex")],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(store.tier_b(self.ctx()).exists())
        self.assertIn("rebuilt", proc.stdout)

    def test_reindex_dry_run_changes_nothing(self):
        self.start("keepme")
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-reindex"), "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0)
        from claude_bestpractice import sessions

        self.assertIsNotNone(sessions.get(self.ctx(), sid(self.repo, "keepme")))


if __name__ == "__main__":
    unittest.main()


class TestTheBoardAsksForWhatOnlyAFounderKnows(GateCase):
    """The layer that asks the founder three questions was never reached.

    `Setup` fires on `--init`; `claude-bp status` is a command the founder runs. On the
    ordinary install path nothing told a session the knowledge layer was missing.
    Verified before this existed: a fresh session on a fresh repository went and edited
    code, and the layer was still absent afterwards.
    """

    def board(self) -> str:
        proc = self.run_hook(
            "session-start", {"session_id": "b1", "hook_event_name": "SessionStart"}
        )
        return proc.stdout

    def test_a_repository_without_a_layer_is_told_so(self):
        out = self.board()
        self.assertIn("no knowledge layer here yet", out)
        self.assertIn("claude-bp init", out)
        self.assertIn("ASK THE FOUNDER", out, "it must not be left to guess them")

    def test_a_repository_with_a_layer_hears_nothing(self):
        """It is one line on the first sessions and silence forever after."""
        from claude_bestpractice import onboard

        onboard.write(self.ctx())
        self.assertNotIn("no knowledge layer here yet", self.board())


class TestAdoptDoesNotWriteADeadProductNameIntoYourSettings(GateCase):
    """The quarantine key was `_founderOsQuarantined` — a name this project shed.

    It lands in the founder's own `.claude/settings.json`, where a reader has no way to
    tell what wrote it or what it belongs to. Found by running `adopt` against a realistic
    competing installation for the first time; a grep for the old name had missed it
    because the identifier is camelCase.
    """

    def settings(self) -> dict:
        import json

        return json.loads((self.repo / ".claude" / "settings.json").read_text())

    def contested(self) -> None:
        import json

        path = self.repo / ".claude" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "./theirs.sh"}]}],
            "PostToolUse": [{"hooks": [{"type": "command", "command": "./log.sh"}]}],
        }}))

    def test_the_key_names_this_project(self):
        from claude_bestpractice import conflicts

        self.contested()
        conflicts.quarantine_loose_hooks(self.ctx())
        keys = [k for k in self.settings() if k != "hooks"]
        self.assertEqual([conflicts.QUARANTINE_KEY], keys)
        self.assertNotIn("founderOs", keys[0])

    def test_an_uncontested_event_is_left_alone(self):
        from claude_bestpractice import conflicts

        self.contested()
        conflicts.quarantine_loose_hooks(self.ctx())
        self.assertEqual(["PostToolUse"], list(self.settings()["hooks"].keys()))


class TestTheEnforcementStateIsNotTheAgentsToEdit(GateCase):
    """`config.json` was refused as "the plugin's own enforcement state" while the OTHER
    half — session records, the baseline the diff is measured from, the block counter —
    stayed writable, and the Stop gate trusts all of it. Reported as issue #32 with two
    working bypasses of a gate that was actively blocking a red suite.

    The threat model is not a malicious founder. It is an agent blocked four times looking
    for the shortest way to end the turn, in a directory whose path this plugin prints on
    its own board, holding plain JSON whose field names say what they do.
    """

    def decide(self, tool: str, tool_input: dict) -> str:
        proc = self.gate(
            "pre-tool",
            {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": tool,
             "tool_input": tool_input, "cwd": str(self.repo)},
        )
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"].get("permissionDecision", "allow")
        except (json.JSONDecodeError, KeyError, TypeError):
            return "allow"

    def test_the_session_record_cannot_be_rewritten(self):
        """Route 1, the serious one: commit the broken change, point `baseline_commit` at
        the new HEAD, and `changed_files` comes back empty — the suite is never run and
        NOTHING is recorded. The next session sees a clean history."""
        from claude_bestpractice import store

        self.start()
        target = store.tier_b(self.ctx(), "sessions", "s1.json")
        self.assertEqual(self.decide("Write", {"file_path": str(target), "content": "{}"}), "deny")

    def test_the_state_directory_cannot_be_deleted(self):
        from claude_bestpractice import store

        self.start()
        self.assertEqual(
            self.decide("Bash", {"command": f"rm -rf {store.tier_b(self.ctx())}"}), "deny")

    def test_the_push_hook_cannot_be_deleted(self):
        from claude_bestpractice import ci

        self.start()
        self.assertEqual(
            self.decide("Bash", {"command": f"rm -f {ci.hook_path(self.ctx())}"}), "deny")

    def test_ordinary_work_is_untouched(self):
        """A rule that also refuses the founder's own files is one they switch off."""
        self.start()
        self.assertEqual(
            self.decide("Write", {"file_path": str(self.repo / "a.py"), "content": "x = 2\n"}),
            "allow",
        )
        task = self.repo / ".claude" / "claude-bestpractice" / "plan" / "next" / "0001.md"
        self.assertEqual(self.decide("Write", {"file_path": str(task), "content": "task"}), "allow")


class TestANodIsNotATaskStatement(GateCase):
    """The statement took the last turn unconditionally, so a task became «Делай».

    Most turns in a real session are continuations, so the field that names what a
    session is doing ended up holding the least informative sentence the founder typed —
    and that sentence reaches the sibling board, `claude-bp status`, every scope-drift
    refusal, the provisioned branch name, and the attempt filed on an unverified finish,
    which is committed. Measured on a live repository, three concurrent sessions whose
    tasks read «Делай», «обнови» and a merge question, the second of them working on a
    branch called `feat/obnovi-70e44134`.

    Following the founder is right and stays. What was wrong is that a nod counted as
    following.
    """

    def statement_after(self, *prompts: str) -> str:
        return self.after_prompts(*prompts).task_statement

    REAL = "перепиши merge.py так, чтобы при конфликте побеждало значение с более свежим timestamp"

    def test_a_continuation_does_not_replace_the_instruction(self):
        self.write("merge.py", "x = 1\n")
        for nod in ("Делай", "ок", "обнови", "go ahead", "continue", "давай"):
            with self.subTest(nod=nod):
                self.assertEqual(self.REAL, self.statement_after(self.REAL, nod))

    def test_a_new_instruction_still_replaces_the_old_one(self):
        self.write("merge.py", "x = 1\n")
        second = "теперь почини экспорт CSV, он падает на пустом наборе"
        self.assertEqual(second, self.statement_after(self.REAL, second))

    def test_paths_from_a_continuation_turn_are_still_collected(self):
        """Only the statement is filtered. A nod that names a file still widens scope."""
        from claude_bestpractice import sessions

        self.write("merge.py", "x = 1\n")
        self.write("export.py", "y = 2\n")
        self.statement_after(self.REAL, "ок, export.py тоже")
        rec = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(["export.py", "merge.py"], rec.task_paths)

    def test_a_short_prompt_that_names_a_file_is_a_statement(self):
        self.write("merge.py", "x = 1\n")
        self.write("export.py", "y = 2\n")
        self.assertEqual("fix export.py", self.statement_after(self.REAL, "fix export.py"))

    def test_the_first_thing_the_founder_says_is_always_kept(self):
        """With nothing recorded yet, «Делай» beats a blank board — and only then."""
        self.assertEqual("Делай", self.statement_after("Делай"))

    def test_the_branch_is_no_longer_named_after_a_nod(self):
        """«Делай» transliterates to `delay`, an English word meaning the opposite."""
        from claude_bestpractice import worktree

        self.write("merge.py", "x = 1\n")
        statement = self.statement_after(self.REAL, "Делай")
        self.assertNotIn("delay", worktree.session_slug(statement, "70e44134"))


class TestTheCeilingCountsALoopNotAnAfternoon(GateCase):
    """Four blocks minutes apart is a loop. Four blocks across five hours is not.

    The commonest reason a session goes quiet mid-block is that Claude stopped answering:
    the five-hour usage limit lands in the middle of a turn and the session resumes hours
    later with the counter exactly where it was. One more block then filed an UNVERIFIED
    finish and a permanent `outcome: failed` attempt against work whose only fault was
    being interrupted — the ceiling firing on a founder's lunch rather than on a loop.
    """

    def blocked_work(self):
        """A committed change with no evidence, and the ceiling constant to measure against."""
        from claude_bestpractice import evidence

        self.start()
        self.write("feature.py", "def go():\n    return 1\n")
        self.commit("add the feature module")
        return evidence.MAX_CONSECUTIVE_BLOCKS

    def counter(self) -> dict:
        from claude_bestpractice import sessions

        return sessions.get(self.ctx(), sid(self.repo, "s1")).tool_signatures

    def age_the_last_block(self, seconds: float) -> None:
        from claude_bestpractice import sessions

        payload = dict(self.counter())
        payload["_last_block_at"] = time.time() - seconds
        sessions.touch(self.ctx(), sid(self.repo, "s1"), tool_signatures=payload)

    def test_blocks_in_quick_succession_still_reach_the_ceiling(self):
        """The ceiling has to keep working, or a wedged session burns turns forever."""
        ceiling = self.blocked_work()
        codes = [self.stop().returncode for _ in range(ceiling + 1)]
        self.assertEqual([2] * ceiling, codes[:-1])
        self.assertEqual(0, codes[-1], "the ceiling stopped firing")

    def test_a_long_silence_starts_the_streak_over(self):
        ceiling = self.blocked_work()
        for _ in range(ceiling):
            self.assertEqual(2, self.stop().returncode)
        self.assertEqual(ceiling, self.counter().get("_consecutive_blocks"))

        self.age_the_last_block(6 * 3600)
        proc = self.stop()
        self.assertEqual(2, proc.returncode, "the interrupted session was finished UNVERIFIED")
        self.assertIn(f"[1/{ceiling}]", proc.stderr)

    def test_a_short_pause_is_still_the_same_streak(self):
        """A founder reading for ten minutes has not started a new attempt."""
        ceiling = self.blocked_work()
        self.stop()
        self.age_the_last_block(600)
        self.assertIn(f"[2/{ceiling}]", self.stop().stderr)

    def test_a_record_written_before_this_existed_is_not_reset(self):
        """No timestamp means an in-flight streak from an older version; keep counting."""
        from claude_bestpractice import sessions

        ceiling = self.blocked_work()
        self.stop()
        payload = {k: v for k, v in self.counter().items() if k != "_last_block_at"}
        sessions.touch(self.ctx(), sid(self.repo, "s1"), tool_signatures=payload)
        self.assertIn(f"[2/{ceiling}]", self.stop().stderr)


class TestProgressIsNotARepeat(RepoCase):
    """Issue #95. Five sequential edits to five different parts of one module are ordinary
    work, and they shared a signature — so the fifth was refused as "run 4 times in a row
    with nothing in between".

    The detector wanted to tell a retry from progress and was not drawing that line: it
    keyed on tool and path, and left out the ANCHOR that says which region an edit names.
    """

    def edit(self, path: str, old: str, new: str) -> str:
        proc = self.run_hook(
            "pre-tool",
            {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(self.repo / path), "old_string": old,
                            "new_string": new}},
        )
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"].get("permissionDecision", "allow")
        except (json.JSONDecodeError, KeyError, TypeError):
            return "allow"

    def test_five_distinct_edits_to_one_file_are_not_a_loop(self):
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        for n in range(6):
            decision = self.edit("module.py", f"def step_{n}():", f"def step_{n}(arg):")
            self.assertNotEqual("deny", decision, f"edit {n} of six distinct regions was refused")

    def test_repeating_one_edit_unchanged_is_still_a_loop(self):
        """The line the detector wanted to draw, still drawn."""
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        decisions = [self.edit("module.py", "def same():", f"def same():  # try {n}")
                     for n in range(6)]
        self.assertIn("deny", decisions, "an unchanged retry was never caught")

    def test_two_heredocs_writing_different_files_are_not_a_loop(self):
        """Truncating at 160 characters made them identical, because the boilerplate that
        opens a heredoc is — so probing for the cause of one block was itself blocked."""
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        preamble = "python3 - <<'PY'\nimport sys\n" + ("# padding\n" * 30)
        for n in range(6):
            proc = self.run_hook(
                "pre-tool",
                {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                 "tool_input": {"command": f"{preamble}open('/tmp/probe{n}.py','w')\nPY"}},
            )
            try:
                decision = json.loads(proc.stdout)["hookSpecificOutput"].get("permissionDecision", "allow")
            except (json.JSONDecodeError, KeyError, TypeError):
                decision = "allow"
            self.assertNotEqual("deny", decision, f"probe {n} was refused as a repeat")


class TestARefusalSaysWhereToLook(RepoCase):
    """Issue #95, second half. A write was refused for "what looks like a credential", the
    founder deleted the lines they suspected, the write was refused again, and nothing told
    them what had matched. The file could not be written by any route.

    Their diagnosis was wrong — the lines they blamed do not fire — which is the point.
    """

    def test_the_line_and_a_scrubbed_excerpt_are_named(self):
        proc = self.run_hook(
            "pre-tool",
            {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
             "tool_input": {"file_path": str(self.repo / "conf.py"),
                            "content": 'SALT_EXTREME = 15.0\nAPI_TOKEN = "sk_live_51H8xQ2eZvKYlo2C0"\n'}},
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("line 2", reason, "the refusal did not say where")
        self.assertNotIn("51H8xQ2eZvKYlo2C0", reason, "the refusal printed the secret it found")

    def test_ordinary_domain_words_do_not_fire_at_all(self):
        """`salt` in a nutrition threshold, `token` in a tokenizer kwarg."""
        from claude_bestpractice import redact

        self.assertEqual([], redact.find("SALT_EXTREME = 15.0"))
        self.assertEqual([], redact.find("out = tok(t, skip_special_tokens=True)"))


class TestACardBeforeTheCode(RepoCase):
    """A card is asked for at the FIRST WRITE, not at the finish.

    The Stop demand asks at the end, so a whole turn of edits happens first and the board
    is true only in retrospect. Asked where a task actually begins instead — and never a
    wedge, because the way out is a Bash command and Bash is not what this refuses.
    """

    def gate(self, name: str, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / name)],
            input=json.dumps({"cwd": str(self.repo), **event}),
            capture_output=True, text=True, cwd=str(self.repo), timeout=120,
        )

    def decision(self, proc) -> str | None:
        try:
            return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def write_event(self, relpath: str) -> dict:
        return {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / relpath), "content": "x = 1\n"}}

    def start(self, task: str = "перепиши импортер каталога, он падает на пустых ценах"):
        """A session the founder has actually given a task — which is when a card is due.

        Without a statement there is no task to card, and the rule deliberately stays
        quiet; the Stop demand still catches that session at the end.
        """
        proc = self.gate("session-start",
                         {"session_id": "s1", "hook_event_name": "SessionStart",
                          "source": "startup"})
        self.gate("prompt-capture",
                  {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": task})
        return proc

    def test_the_first_write_without_a_card_is_refused(self):
        self.start()
        proc = self.gate("pre-tool", self.write_event("backend/new.py"))
        self.assertEqual("deny", self.decision(proc), proc.stdout)
        self.assertIn("claude-bp-plan add", proc.stdout, "a refusal must name the way through")

    def test_the_card_the_refusal_names_clears_it(self):
        self.start()
        self.claim_a_task("s1", "backend/new.py")
        proc = self.gate("pre-tool", self.write_event("backend/new.py"))
        self.assertEqual("allow", self.decision(proc), proc.stdout + proc.stderr)

    def test_a_session_the_founder_has_not_briefed_is_not_asked_for_a_card(self):
        """A card records work somebody asked for. Demanding one where nothing was asked
        is the gate inventing a process rather than recording one."""
        self.gate("session-start", {"session_id": "s1", "hook_event_name": "SessionStart",
                                    "source": "startup"})
        proc = self.gate("pre-tool", self.write_event("backend/new.py"))
        self.assertNotEqual("deny", self.decision(proc), proc.stdout)

    def test_the_ledgers_own_files_never_need_a_card(self):
        """Otherwise filing the card is refused by the rule demanding one."""
        self.start()
        proc = self.gate("pre-tool", self.write_event(".claude/claude-bestpractice/x.json"))
        self.assertNotEqual("deny", self.decision(proc), proc.stdout)
