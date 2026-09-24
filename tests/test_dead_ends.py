"""Issue #220, the half that is about what a refusal SAYS.

Three defects in one day where the prescribed action was impossible, and the founder's
proposal as an invariant: "у каждого отказа обязана существовать хотя бы одна команда,
которую сессия может выполнить прямо сейчас. Если её нет — это не гейт, а тупик."

Also §3 — `git add -A` in a shared checkout — and §4 — a red suite judged against the
trunk before it is blamed on the session.
"""

from __future__ import annotations

import os
import subprocess
import unittest

from helpers import BIN, RepoCase, git, sid

from claude_bestpractice import evidence, gitpolicy, plan, sessions


class TestStagingTheWholeTree(RepoCase):
    """The accident was caught by hand: `git add -A` in a shared checkout pulled ~50 ledger
    files from a dozen branches into one commit, one `reset --soft` from being merged."""

    def setUp(self) -> None:
        super().setUp()
        self.me = sid(self.repo, "mine")
        self.them = sid(self.repo, "theirs")
        self.write("src/mine.py", "x = 1\n")
        self.write("src/theirs.py", "y = 1\n")
        self.commit("both files exist")

    def a_sibling_holding(self, relpath: str) -> None:
        record = self.session_record(self.them)
        sessions.register(self.ctx(), record)
        self.assertIsNone(sessions.acquire_lease(self.ctx(), self.them, relpath))

    def add_everything(self, command: str = "git add -A"):
        return self.run_hook("pre-tool", {
            "session_id": "mine", "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command},
        })

    def test_a_siblings_file_in_the_tree_refuses_the_sweep(self):
        self.a_sibling_holding("src/theirs.py")
        self.write("src/mine.py", "x = 2\n")
        self.write("src/theirs.py", "y = 2\n")

        proc = self.add_everything()
        self.assertEqual("deny", self.hook_decision(proc))
        reason = self.hook_reason(proc)
        self.assertIn("src/theirs.py", reason)
        self.assertIn("git add -- src/mine.py", reason, "it refused without naming the way out")

    def test_only_my_own_changes_are_not_refused(self):
        self.a_sibling_holding("src/theirs.py")
        self.write("src/mine.py", "x = 2\n")
        self.assertNotEqual("deny", self.hook_decision(self.add_everything()))

    def test_naming_paths_explicitly_is_never_refused(self):
        self.a_sibling_holding("src/theirs.py")
        self.write("src/theirs.py", "y = 2\n")
        self.assertNotEqual(
            "deny", self.hook_decision(self.add_everything("git add -- src/mine.py")))

    def test_a_dead_siblings_claim_does_not_block_anything(self):
        """A lease outlives the session that took it, and the sweep that clears it runs at
        somebody's session start. Until then it must not be somebody else's refusal."""
        self.write("src/theirs.py", "y = 2\n")
        record = self.session_record(self.them, pid=999_999)
        sessions.register(self.ctx(), record)
        sessions.acquire_lease(self.ctx(), self.them, "src/theirs.py")
        self.assertNotEqual("deny", self.hook_decision(self.add_everything()))

    def test_ledger_files_in_the_index_refuse_it(self):
        """A clone that still tracks the ledger, mid-upgrade. Everybody's files and nobody's,
        which is the whole argument for taking them out of git (#219)."""
        plan.add(self.ctx(), "somebody else's card", paths=["src/theirs.py"], done_when="stated")
        foreign, _mine = gitpolicy.whose_work_is_in_the_way(self.ctx(), self.me)
        self.assertTrue(any(".claude/claude-bestpractice/plan/" in path for path in foreign), foreign)

    def test_a_clean_tree_refuses_nothing(self):
        self.a_sibling_holding("src/theirs.py")
        self.assertNotEqual("deny", self.hook_decision(self.add_everything()))

    def test_a_sibling_card_is_read_as_well_as_a_lease(self):
        """A session that claimed a card and has not written yet holds no lease. Its paths
        are still not somebody else's to commit."""
        record = self.session_record(self.them)
        record.task_paths = ["src/theirs.py"]
        sessions.register(self.ctx(), record)
        self.write("src/theirs.py", "y = 2\n")

        foreign, _mine = gitpolicy.whose_work_is_in_the_way(self.ctx(), self.me)
        self.assertEqual(["src/theirs.py"], foreign)


class TestARedSuiteIsJudgedAgainstTheTrunk(RepoCase):
    """"Прежде чем винить сессию, сверять — падают ли те же тесты на origin/main." A shared
    checkout three commits behind failed four tests that were green on the trunk, and the
    gate told a session whose work was merged that its suite was red (#217, #220)."""

    def setUp(self) -> None:
        super().setUp()
        self.write("tests/test_theirs.py", "def test_a():\n    assert True\n")
        self.write("src/mine.py", "x = 1\n")
        self.commit("a history")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)

    def test_a_tree_level_with_the_trunk_says_nothing(self):
        self.assertEqual("", evidence.behind_the_trunk(self.ctx()))

    def test_a_failing_file_the_trunk_does_not_have_is_named_as_the_trees_lag(self):
        # The trunk moves on; this tree does not, and its copy of the failing test is the
        # one the trunk has already replaced.
        self.write("tests/test_theirs.py", "def test_a():\n    assert True  # fixed upstream\n")
        self.commit("the trunk fixes it")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        git(["reset", "--hard", "-q", "HEAD~1"], self.repo)

        said = evidence.not_this_trees_code(self.ctx(), ["tests/test_theirs.py"], ["src/mine.py"])
        self.assertIn("tests/test_theirs.py", said)
        self.assertIn("--ff-only", said)

    def test_a_failure_in_a_file_this_session_changed_is_this_sessions(self):
        self.write("tests/test_theirs.py", "def test_a():\n    assert False\n")
        said = evidence.not_this_trees_code(
            self.ctx(), ["tests/test_theirs.py"], ["tests/test_theirs.py"])
        self.assertEqual("", said, "it excused a failure in the session's own diff")

    def test_a_failure_on_code_identical_to_the_trunk_says_the_trunk_is_red(self):
        said = evidence.not_this_trees_code(self.ctx(), ["tests/test_theirs.py"], ["src/mine.py"])
        self.assertIn("identical to", said)
        self.assertNotIn("--ff-only", said, "there is nothing to fast-forward to")

    def test_it_says_nothing_without_a_trunk_to_compare_against(self):
        git(["branch", "-D", "origin/main"], self.repo)
        self.assertEqual("", evidence.not_this_trees_code(
            self.ctx(), ["tests/test_theirs.py"], ["src/mine.py"]))

    def test_it_says_nothing_when_no_failing_file_is_known(self):
        self.assertEqual("", evidence.not_this_trees_code(self.ctx(), [], ["src/mine.py"]))

    def test_a_name_git_would_quote_or_split_is_compared_whole(self):
        """Read split on spaces and quoted, these came back as names that match nothing, and
        the refusal called the trunk red over files that differ from it."""
        self.write("tests/test café.py", "def test_a():\n    assert True\n")
        self.write("tests/test_naïve.py", "def test_a():\n    assert True\n")
        self.commit("names git quotes")
        git(["branch", "-f", "origin/main", "HEAD"], self.repo)
        self.write("tests/test café.py", "def test_a():\n    assert False\n")
        self.write("tests/test_naïve.py", "def test_a():\n    assert False\n")
        self.commit("this tree's own edits to them")

        said = evidence.not_this_trees_code(
            self.ctx(), ["tests/test café.py", "tests/test_naïve.py"], ["src/mine.py"])
        self.assertNotIn("identical to", said)


class TestAFailingRunsOutputIsReadSafely(RepoCase):
    """The files a failure is about are read out of text the gated project printed."""

    def test_a_name_longer_than_a_file_name_does_not_crash_the_gate(self):
        """A test about over-long names printed one; `is_file` raised ENAMETOOLONG out of a
        gate that fails closed, so every Stop said "gate failed (OSError ...)", the counter
        never moved, and the failure itself was never shown."""
        from claude_bestpractice import sessions

        self.configure(require_task=False, manage_pull_requests=False)
        self.write("store.py", "def accepts(name):\n    return len(name) <= 255\n")
        self.write("tests/test_store.py", (
            "from store import accepts\n\n\ndef test_rejects_overlong_names():\n"
            "    name = 'export_' + 'x' * 300 + '.py'\n    assert not accepts(name), name\n"))
        self.commit("a limit on names")
        self.write("store.py", "def accepts(name):\n    return True\n")

        proc = self.run_hook("evidence-gate", {"session_id": "s1", "hook_event_name": "Stop",
                                               "stop_hook_active": False})
        self.assertEqual(2, proc.returncode)
        self.assertNotIn("gate failed", proc.stderr)
        self.assertIn("FAILS", proc.stderr)
        record = sessions.get(self.ctx(), sid(self.repo, "s1"))
        self.assertEqual(1, record.tool_signatures.get("_consecutive_blocks"),
                         "the escalation counter did not move")

    def test_an_extension_is_the_whole_extension(self):
        """`app.tsx` was read as `app.ts` and then not found; `.json` was read as `.js`."""
        self.write("src/app.tsx", "export const x = 1\n")
        self.write("data/export.json", "{}\n")
        self.write("data/export.js", "module.exports = {}\n")
        verdict = evidence.Verdict(False, "FAILED src/app.tsx:3 — reading data/export.json")
        self.assertEqual(["src/app.tsx"], evidence.failing_files(self.ctx(), verdict))


if __name__ == "__main__":
    unittest.main()


class TestTheInvariantIsChecked(unittest.TestCase):
    """The founder's proposal as a mechanism: doctor check 35 provokes real refusals and
    fails when one names nothing that could run. Here the extractor it leans on is held to
    both shapes this plugin writes refusals in, and to the shape that is a dead end."""

    def doctor(self):
        """The doctor loaded as a module. It has no `.py`, being an executable gate.

        Registered in `sys.modules` before it is executed: `@dataclass` resolves the
        module's own annotations through there, and a module absent from it fails on its
        first decorator.
        """
        import importlib.machinery
        import importlib.util
        import sys

        from helpers import BIN

        loader = importlib.machinery.SourceFileLoader(
            "doctor_under_test", str(BIN / "claude-bp-doctor"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        self.addCleanup(sys.modules.pop, loader.name, None)
        loader.exec_module(module)
        return module

    def test_a_backticked_command_counts(self):
        found = self.doctor()._runnable_in("refused. `git worktree remove /tmp/x` and carry on.")
        self.assertEqual(["git worktree remove /tmp/x"], found)

    def test_an_indented_command_counts(self):
        found = self.doctor()._runnable_in("refused:\n  git add -- src/a.py\nthen commit.")
        self.assertEqual(["git add -- src/a.py"], found)

    def test_a_command_on_an_indented_line_is_not_swallowed_by_it(self):
        """The bug this check found in itself on its first run: one alternation consumed the
        whole indented line, so the backticked command inside it never matched."""
        found = self.doctor()._runnable_in(
            '  A GATE refused this; if it is wrong, `claude-bp-report defect "why"` files it.')
        self.assertEqual(['claude-bp-report defect "why"'], found)

    def test_prose_alone_is_a_dead_end(self):
        found = self.doctor()._runnable_in(
            "refusing this write. Put it in an environment variable and reference it.")
        self.assertEqual([], found)

    def test_a_program_that_is_not_installed_here_is_a_dead_end(self):
        """#216, exactly: the refusal named `claude-bp database`, whose own command died on a
        missing `psql`. A command nobody can run is not an exit."""
        found = self.doctor()._runnable_in("run `definitely-not-a-real-runner --fix` first")
        self.assertEqual([], found)

    def test_the_harness_tools_count(self):
        found = self.doctor()._runnable_in("move there: `EnterWorktree /tmp/tree`")
        self.assertEqual(["EnterWorktree /tmp/tree"], found)


class TestTheSweepIsJudgedWhereItWouldRun(RepoCase):
    """A session with its own tree stages in THAT tree. Judged against the shared checkout
    instead, a clean tree would be refused over files it cannot see — which would make this
    gate the very thing #220 is about, one level down."""

    def test_a_session_in_its_own_tree_is_not_refused_over_the_shared_checkout(self):
        from claude_bestpractice import worktree

        me = sid(self.repo, "mine")
        them = sid(self.repo, "theirs")
        self.write("src/theirs.py", "y = 1\n")
        self.commit("a history")
        tree = worktree.provision(self.ctx(), "my own work", me)
        record = self.session_record(them)
        sessions.register(self.ctx(), record)
        sessions.acquire_lease(self.ctx(), them, "src/theirs.py")
        # Dirty in the SHARED checkout, and nothing at all in this session's tree.
        self.write("src/theirs.py", "y = 2\n")

        found, _mine = gitpolicy.whose_work_is_in_the_way(
            worktree.working_context(self.ctx(), me), me)
        self.assertEqual([], found, f"judged the wrong tree: {found}")
        self.assertTrue(tree.is_dir())


class TestTheCardARefusalAsksForIsFiledAsPrinted(RepoCase):
    """The missing-card refusals print `claude-bp-plan add "<…>" --paths X`, then
    `claude-bp-plan claim <id>` — and `claim` refuses a card with no `--done-when`, so the
    second command failed on the card the first had just filed. The `git merge` refusal's
    `add` did not even parse: `--paths <the files this touches>` is a redirect to bash.

    Run here the way a session runs them: each printed line through a shell, with this
    plugin's own commands on PATH, and then the refused call again.
    """

    def run_as_printed(self, refusal: str) -> None:
        env = dict(os.environ, PATH=f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}",
                   CLAUDE_CODE_SESSION_ID="s1")
        lines = [line.strip() for line in refusal.splitlines()]
        add = next(line for line in lines if line.startswith("claude-bp-plan add"))
        filed = subprocess.run(["bash", "-c", add], cwd=self.repo, env=env,
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(0, filed.returncode, f"{add}\n{filed.stderr}")
        then = next(line for line in lines if line.startswith("then: claude-bp-plan claim"))
        claim = then.split(":", 1)[1].split("(", 1)[0].strip().replace("<id>", filed.stdout.split()[0])
        claimed = subprocess.run(["bash", "-c", claim], cwd=self.repo, env=env,
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(0, claimed.returncode, f"{claim}\n{claimed.stderr}")

    def pre_tool(self, tool_name: str, tool_input: dict):
        return self.run_hook("pre-tool", {"session_id": "s1", "hook_event_name": "PreToolUse",
                                          "tool_name": tool_name, "tool_input": tool_input})

    def briefed(self) -> None:
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.run_hook("prompt-capture", {"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                                         "prompt": "add a csv export to the billing report"})

    def test_the_write_refusal_on_a_path_with_a_space_in_it(self):
        self.briefed()
        write = {"file_path": str(self.repo / "src" / "csv export.py"), "content": "x = 1\n"}
        refused = self.pre_tool("Write", write)
        self.assertEqual("deny", self.hook_decision(refused), refused.stdout)
        self.run_as_printed(self.hook_reason(refused))
        self.assertNotEqual("deny", self.hook_decision(self.pre_tool("Write", write)))

    def test_the_refusal_of_work_that_writes_no_file(self):
        git(["branch", "feat/theirs"], self.repo)
        self.briefed()
        merge = {"command": "git merge feat/theirs"}
        refused = self.pre_tool("Bash", merge)
        self.assertEqual("deny", self.hook_decision(refused), refused.stdout)
        self.run_as_printed(self.hook_reason(refused))
        self.assertNotEqual("deny", self.hook_decision(self.pre_tool("Bash", merge)))

    def test_the_demand_at_the_finish(self):
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.write("src/billing.py", "TOTAL = 1\n")
        stop = {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False}
        demanded = self.run_hook("evidence-gate", stop)
        self.assertIn("Nothing on the board", demanded.stderr)
        self.run_as_printed(demanded.stderr)
        self.assertNotIn("Nothing on the board", self.run_hook("evidence-gate", stop).stderr)
