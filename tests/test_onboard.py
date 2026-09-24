"""Auto-onboarding, worktree provisioning, the status view, and the slop checker."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, REPO_ROOT, RepoCase

from claude_bestpractice import knowledge, onboard

SLOP = REPO_ROOT / "tools" / "check_slop.py"


class TestDetection(RepoCase):
    def test_detects_a_python_stack(self):
        self.write("pyproject.toml", "[project]\nname = 'x'\n")
        self.assertIn("python", onboard.detect(self.ctx()).stack)

    def test_detects_a_framework_from_the_manifest(self):
        self.write("package.json", '{"dependencies": {"next": "14.0.0"}}')
        found = onboard.detect(self.ctx())
        self.assertIn("node", found.stack)
        self.assertIn("Next.js", found.frameworks)

    def test_notices_tests(self):
        self.write("tests/test_a.py", "def test_a():\n    assert True\n")
        self.assertTrue(onboard.detect(self.ctx()).has_tests)

    def test_picks_central_types_as_entities(self):
        """Structural centrality, not name matching: what everything touches matters."""
        self.write("models.py", "class Invoice:\n    pass\n\n\nclass Client:\n    pass\n")
        self.write(
            "api.py",
            "from models import Invoice, Client\n\n\ndef create() -> Invoice:\n"
            "    return Invoice()\n\n\ndef lookup() -> Client:\n    return Client()\n",
        )
        self.write(
            "billing.py",
            "from models import Invoice\n\n\ndef charge(i: Invoice) -> None:\n    print(Invoice)\n",
        )
        names = {name for name, _ in onboard.detect(self.ctx()).entities}
        self.assertIn("Invoice", names)

    def test_ignores_types_nothing_references(self):
        self.write("lonely.py", "class NeverUsed:\n    pass\n")
        names = {name for name, _ in onboard.detect(self.ctx()).entities}
        self.assertNotIn("NeverUsed", names)

    def test_empty_repo_detects_nothing_and_does_not_raise(self):
        found = onboard.detect(self.ctx())
        self.assertEqual(found.entities, [])
        self.assertEqual(found.file_count, 0)


class TestWriting(RepoCase):
    def seed_code(self) -> None:
        self.write("models.py", "class Invoice:\n    pass\n")
        self.write(
            "api.py",
            "from models import Invoice\n\n\ndef create() -> Invoice:\n    return Invoice()\n",
        )
        self.write("billing.py", "from models import Invoice\n\n\ndef charge():\n    return Invoice\n")

    def test_writes_the_three_files(self):
        self.seed_code()
        written = onboard.write(self.ctx())
        self.assertEqual(len(written), 3)
        root = self.repo
        self.assertTrue((root / knowledge.RULES_DIR / knowledge.PRODUCT).exists())
        self.assertTrue((root / knowledge.DOMAIN_DIR / knowledge.ENTITIES).exists())
        self.assertTrue((root / knowledge.RULES_DIR / knowledge.INDEX).exists())

    def test_only_writes_anchors_that_resolve(self):
        """An unverified anchor is a future lie, so it is never written."""
        self.seed_code()
        onboard.write(self.ctx())
        text = (self.repo / knowledge.DOMAIN_DIR / knowledge.ENTITIES).read_text()
        for line in text.splitlines():
            if line.strip().startswith("code:"):
                anchor = line.split(":", 1)[1].strip()
                self.assertTrue(knowledge.anchor_resolves(self.ctx(), anchor), anchor)

    def test_never_overwrites_an_answered_file(self):
        self.write(f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}", "# Product\n\nMy real answer.\n")
        onboard.write(self.ctx())
        self.assertIn(
            "My real answer", (self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).read_text()
        )

    def test_force_regenerates_what_nobody_answered(self):
        """A template whose slots are all still slots, derived from a repository that has
        since grown code: nothing in it is anybody's, and `--force` brings it up to date."""
        onboard.write(self.ctx())
        entities = self.repo / knowledge.DOMAIN_DIR / knowledge.ENTITIES
        self.assertNotIn("Invoice", entities.read_text())
        self.seed_code()
        onboard.write(self.ctx(), force=True)
        self.assertIn("Invoice", entities.read_text())
        self.assertIn("3 source files", (self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).read_text())

    def test_force_never_takes_an_answer(self):
        """`claude-bp init` said "use --force to regenerate", and `--force` replaced an
        answered product.md and glossary with placeholders, keeping no copy."""
        self.seed_code()
        onboard.write(self.ctx())
        product = self.repo / knowledge.RULES_DIR / knowledge.PRODUCT
        glossary = self.repo / knowledge.RULES_DIR / knowledge.GLOSSARY
        product.write_text(product.read_text().replace(
            "<ANSWER THIS. One or two sentences: the thing a user gets, not the tech.>",
            "Payroll for bakeries."))
        # Half a line is an answer too: the definition is theirs, the synonyms still a slot.
        glossary.write_text(glossary.read_text().replace("Invoice — <definition>.",
                                                         "Invoice — what a bakery bills."))
        onboard.write(self.ctx(), force=True)
        self.assertIn("Payroll for bakeries.", product.read_text())
        self.assertIn("what a bakery bills", glossary.read_text())

    def test_init_force_names_what_it_kept(self):
        subprocess.run([sys.executable, str(BIN / "claude-bp"), "init"],
                       capture_output=True, text=True, cwd=str(self.repo), timeout=180)
        product = self.repo / knowledge.RULES_DIR / knowledge.PRODUCT
        product.write_text(product.read_text() + "\nSold to bakeries.\n")
        said = subprocess.run([sys.executable, str(BIN / "claude-bp"), "init", "--force"],
                              capture_output=True, text=True, cwd=str(self.repo), timeout=180).stdout
        self.assertIn("kept, because they carry your answers", said)
        self.assertIn(f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}", said.split("kept,")[1])

    def test_product_is_not_invented(self):
        """A fabricated product description is worse than none: the agent believes it."""
        self.seed_code()
        onboard.write(self.ctx())
        text = (self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).read_text()
        self.assertIn("ANSWER THIS", text)

    def test_unanswered_reports_the_gaps(self):
        self.seed_code()
        onboard.write(self.ctx())
        gaps = onboard.unanswered(self.ctx())
        self.assertTrue(any("product.md" in gap for gap in gaps))

    def test_answered_layer_reports_no_gaps(self):
        self.write(
            f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}",
            "# Product\n\n## What this is\nA ledger.\n\n## Non-goals\n- Payroll\n",
        )
        self.write(f"{knowledge.DOMAIN_DIR}/{knowledge.ENTITIES}", "# nothing yet\n")
        self.write(f"{knowledge.RULES_DIR}/{knowledge.GLOSSARY}", "# Glossary\n")
        self.assertEqual(onboard.unanswered(self.ctx()), [])


class TestTheFirstThingAFreshInstallSays(RepoCase):
    """Two small untruths in the output a founder reads before anything else."""

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=300,
        )

    def test_a_layer_that_was_never_built_is_not_called_broken(self):
        """`Repair the knowledge layer` was printed about something that did not exist."""
        out = self.cli("status").stdout
        self.assertIn("`claude-bp init`", out)
        self.assertNotIn("Repair the knowledge layer", out)

    def test_a_built_layer_is_no_longer_offered_the_build_advice(self):
        self.cli("init")
        out = self.cli("status").stdout
        self.assertNotIn("Build the knowledge layer", out)

    def test_a_template_is_not_announced_as_derived(self):
        """`render_entities` emits a commented template when nothing was central enough.

        Honest file, dishonest summary: `init` listed it under "derived from your code".
        """
        self.write("app.py", "def add(a, b):\n    return a + b\n")
        out = self.cli("init").stdout
        self.assertIn("nothing could be derived", out)
        derived, _, awaiting = out.partition("nothing could be derived")
        self.assertIn("entities.yaml", awaiting)
        self.assertNotIn("entities.yaml", derived)


class TestALayerInAnotherShapeIsNotAnAbsentOne(RepoCase):
    """The plugin put its own layer in `.claude/rules/` and then judged the layer by
    whether its own four files were there. A repository with `CLAUDE.md` and eight rule
    files in that exact directory was told, every session, to run `claude-bp init` — which
    from the founder's side is being told to start what they finished months ago (#112)."""

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=300,
        )

    def theirs(self, count: int = 8) -> None:
        self.write("CLAUDE.md", "# Project\n\nAlways keep the ledger current.\n")
        for name in ("alerts", "code-style", "memory", "preferences", "releasing",
                     "scoring", "security", "testing")[:count]:
            self.write(f".claude/rules/{name}.md", f"# {name}\n\nAlways do the {name} thing.\n")

    def test_an_empty_repository_still_reads_as_empty(self):
        self.assertEqual(onboard.NONE, onboard.shape(self.ctx()))

    def test_instruction_files_in_another_shape_are_seen(self):
        self.theirs()
        self.assertEqual(onboard.ANOTHER, onboard.shape(self.ctx()))
        self.assertEqual(9, len(knowledge.existing_rules(self.ctx())))

    def test_the_plugins_own_files_are_not_counted_as_the_founders(self):
        self.cli("init")
        self.assertEqual(onboard.OURS, onboard.shape(self.ctx()))
        names = {path.name for path in knowledge.existing_rules(self.ctx())}
        self.assertNotIn(knowledge.PRODUCT, names)
        self.assertNotIn(knowledge.GLOSSARY, names)
        self.assertNotIn(knowledge.INDEX, names)

    def test_the_board_stops_saying_there_is_none(self):
        self.theirs()
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("no knowledge layer here yet", context)
        self.assertIn("9 instruction file(s) already here", context)

    def test_the_board_still_says_so_when_there_genuinely_is_none(self):
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("no knowledge layer here yet", context)

    def test_what_the_founders_layer_costs_every_turn_is_counted(self):
        """This plugin itemises its own always-on context to the byte and holds itself
        under 400 tokens. The files it sits beside were counted for `CLAUDE.md` alone."""
        self.theirs()
        self.assertGreater(knowledge.instruction_bytes(self.ctx()), 0)
        self.assertIn("bytes in every turn", self.cli("status").stdout)

    def test_a_layer_that_was_never_built_here_is_not_reported_as_broken(self):
        self.theirs()
        out = self.cli("status").stdout
        self.assertNotIn("product.md: missing", out)
        self.assertNotIn("Build the knowledge layer", out)


class TestSetupHook(RepoCase):
    def setup_hook(self):
        return self.run_hook("setup", {"session_id": "s1", "hook_event_name": "Setup"})

    def test_setup_provisions_a_fresh_repository(self):
        self.write("models.py", "class Invoice:\n    pass\n")
        self.write("api.py", "from models import Invoice\n\n\ndef f():\n    return Invoice\n")
        proc = self.setup_hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Detected stage", body)
        self.assertIn("waiting on a human answer", body)
        self.assertTrue((self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).exists())

    def test_setup_is_idempotent(self):
        self.setup_hook()
        self.write(f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}", "# Product\n\nmine\n")
        self.setup_hook()
        self.assertIn("mine", (self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).read_text())


class TestWorktreeCreate(RepoCase):
    def hook(self, **extra) -> subprocess.CompletedProcess:
        return self.run_hook(
            "worktree-create",
            {"session_id": "s1", "hook_event_name": "WorktreeCreate", **extra},
            env={"HOME": str(self.tmp), "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )

    def test_returns_a_path_the_cli_enters_without_asking(self):
        """`EnterWorktree` prompts unconditionally outside `.claude/worktrees/`, before
        permissions are consulted — so a tree anywhere else is one the founder authorises
        every time this plugin orders the move (#111). The reason these used to sit outside
        the repository is paid by the exclude, asserted in test_gitpolicy."""
        proc = self.hook(branch="feature-x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = proc.stdout.strip()
        self.assertTrue(path)
        self.assertTrue(
            path.startswith(str((self.repo / ".claude" / "worktrees").resolve()) + "/"), path)

    def test_honours_an_explicit_path(self):
        target = self.tmp / "explicit"
        self.assertEqual(self.hook(path=str(target)).stdout.strip(), str(target.resolve()))

    def test_seeds_trust(self):
        """Without trust, project settings, hooks and the status line silently never run."""
        self.hook(branch="feature-y")
        config = json.loads((self.tmp / ".claude.json").read_text())
        entries = list(config["projects"].values())
        self.assertTrue(any(e.get("hasTrustDialogAccepted") for e in entries))

    def test_trees_made_at_once_are_all_trusted(self):
        """Four isolation agents started in one message each wrote back the `~/.claude.json`
        they had read, and one trust survived: three trees whose hooks never ran."""
        from helpers import hooks_at_once

        (self.tmp / ".claude.json").write_text(json.dumps({"numStartups": 7}))

        made = hooks_at_once("worktree-create", [
            {"session_id": "s1", "hook_event_name": "WorktreeCreate", "name": f"agent-{i}"}
            for i in range(4)
        ], self.repo, env={"HOME": str(self.tmp), "PATH": "/usr/bin:/bin:/usr/local/bin"})

        config = json.loads((self.tmp / ".claude.json").read_text())
        trusted = {path for path, entry in config["projects"].items()
                   if entry.get("hasTrustDialogAccepted")}
        self.assertEqual({path.strip() for path in made}, trusted)
        self.assertEqual(7, config["numStartups"], "a key that was not ours was lost")

    def test_every_trust_in_a_burst_is_kept(self):
        """The same race without the `git worktree add` in front of it, which spreads the
        writes out and lets a lost update slip past now and then."""
        import os
        import time

        from helpers import LIB

        code = ("import sys, time; from claude_bestpractice import worktree; "
                "time.sleep(max(0.0, float(sys.argv[2]) - time.time())); "
                "worktree.trust(sys.argv[1])")
        start = str(time.time() + 1.5)
        env = {**os.environ, "HOME": str(self.tmp), "PYTHONPATH": str(LIB)}
        burst = [subprocess.Popen([sys.executable, "-c", code, f"/trees/t{i}", start], env=env)
                 for i in range(6)]
        for proc in burst:
            proc.wait()

        config = json.loads((self.tmp / ".claude.json").read_text())
        self.assertEqual({f"/trees/t{i}" for i in range(6)}, set(config["projects"]))

    def test_derives_a_deterministic_port_and_database(self):
        """Worktrees isolate files but share the daemon, ports and caches."""
        self.hook(branch="feature-z")
        from claude_bestpractice import store

        record = store.read_json(store.tier_b(self.ctx(), "worktrees", "feature-z.json"))
        self.assertIsNotNone(record)
        self.assertTrue(41000 <= record["port"] < 41900)
        self.assertIn("feature_z", record["database"])

    def test_two_branches_get_different_ports(self):
        self.hook(branch="alpha")
        self.hook(branch="beta")
        from claude_bestpractice import store

        ctx = self.ctx()
        a = store.read_json(store.tier_b(ctx, "worktrees", "alpha.json"))
        b = store.read_json(store.tier_b(ctx, "worktrees", "beta.json"))
        self.assertNotEqual(a["port"], b["port"])


class TestStatusCommand(RepoCase):
    def status(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), "status"],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )

    def test_reports_every_section(self):
        proc = self.status()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for section in ("SESSIONS", "PLAN", "KNOWLEDGE", "MEMORY HEALTH", "NEXT ACTION"):
            self.assertIn(section, proc.stdout)

    def test_points_at_unanswered_placeholders_first(self):
        self.write("models.py", "class Invoice:\n    pass\n")
        onboard.write(self.ctx())
        self.assertIn("Answer the placeholders", self.status().stdout)

    def test_init_derives_and_reports(self):
        self.write("models.py", "class Invoice:\n    pass\n")
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp"), "init"],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("derived from your code", proc.stdout)
        self.assertIn("waiting on you", proc.stdout)


class TestTheMachineWideCommandsRunAnywhere(unittest.TestCase):
    """`claude-bp doctor`, `statusline` and `policy --prune` are about this machine: the
    doctor builds throwaway repositories of its own, and the other two touch only the
    settings in the home directory. Each refused with "not inside a git repository"."""

    def setUp(self) -> None:
        import shutil
        import tempfile
        from pathlib import Path

        self.tmp = Path(tempfile.mkdtemp(prefix="claude-bestpractice-anywhere-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.nowhere = self.tmp / "not-a-repository"
        self.home.mkdir()
        self.nowhere.mkdir()

    def claude_bp(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        import os

        return subprocess.run([sys.executable, str(BIN / "claude-bp"), *args],
                              capture_output=True, text=True, cwd=str(self.nowhere),
                              timeout=timeout, env={**os.environ, "HOME": str(self.home)})

    def test_the_status_line_and_the_prune(self):
        for args in (["statusline"], ["statusline", "--install"], ["policy", "--prune"]):
            with self.subTest(args=args):
                proc = self.claude_bp(*args)
                self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        settings = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertIn("claude-bp-statusline", settings["statusLine"]["command"])

    def test_the_doctor(self):
        proc = self.claude_bp("doctor", timeout=600)
        self.assertNotIn("not inside a git repository", proc.stderr)
        self.assertIn("checks passed", proc.stdout)


class TestSlopChecker(RepoCase):
    def slop(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SLOP), *args],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=180,
        )

    def test_catches_a_swallowed_exception(self):
        self.write("bad.py", "def f():\n    try:\n        go()\n    except Exception:\n        pass\n")
        proc = self.slop("--all")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("swallowed_exception", proc.stdout)

    def test_catches_a_bare_except(self):
        self.write("bad.py", "def f():\n    try:\n        go()\n    except:\n        return 1\n")
        self.assertEqual(self.slop("--all").returncode, 1)

    def test_catches_an_unused_parameter(self):
        self.write("bad.py", "def f(a: int, unused: str) -> int:\n    return a\n")
        proc = self.slop("--all")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unused_parameter", proc.stdout)

    def test_ignores_underscore_and_self(self):
        self.write(
            "ok.py",
            "class C:\n    def m(self, _ignored: int) -> int:\n        return 1\n",
        )
        self.assertEqual(self.slop("--all").returncode, 0, self.slop("--all").stdout)

    def test_catches_a_compat_shim(self):
        self.write("bad.py", "def handler_v2() -> int:\n    return 1\n")
        proc = self.slop("--all")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("compat_shim", proc.stdout)

    def test_allow_compat_disables_that_class(self):
        """The rule turns itself off once the project has real external consumers."""
        self.write("bad.py", "def handler_v2() -> int:\n    return 1\n")
        self.assertEqual(self.slop("--all", "--allow-compat").returncode, 0)

    def test_does_not_flag_its_own_pattern_definitions(self):
        """A checker that fails on its own source is one nobody trusts.

        Asserts on the finding tag `[compat_shim]`, not the bare word — the clean
        summary line legitimately contains `compat_shims=0/0`, and matching that would
        make this test pass and fail for the wrong reasons.
        """
        proc = subprocess.run(
            [sys.executable, str(SLOP), "--all"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("[compat_shim]", proc.stdout)

    def test_first_ratchet_seeds_the_baseline(self):
        """A ratchet seeded at zero can never be satisfied and gets disabled on day one."""
        self.write(
            "long.py",
            "def f():\n" + "\n".join(f"    x{i} = {i}" for i in range(80)) + "\n    return 1\n",
        )
        self.assertEqual(self.slop("--all").returncode, 1)
        self.assertEqual(self.slop("--all", "--ratchet").returncode, 0)
        self.assertEqual(self.slop("--all").returncode, 0)

    def test_defect_classes_are_never_seeded(self):
        """Structural debt is baselined; a swallowed exception is never granted an allowance."""
        self.write("bad.py", "def f():\n    try:\n        go()\n    except Exception:\n        pass\n")
        self.slop("--all", "--ratchet")
        self.assertEqual(self.slop("--all").returncode, 1)

    def test_ratchet_only_lowers(self):
        self.write(
            "long.py",
            "def f():\n" + "\n".join(f"    x{i} = {i}" for i in range(80)) + "\n    return 1\n",
        )
        self.slop("--all", "--ratchet")
        (self.repo / "long.py").unlink()
        self.slop("--all", "--ratchet")
        budget = json.loads((self.repo / ".claude/claude-bestpractice/slop-budget.json").read_text())
        self.assertEqual(budget["long_functions"], 0)


if __name__ == "__main__":
    unittest.main()
