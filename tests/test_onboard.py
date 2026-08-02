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

    def test_force_regenerates(self):
        self.write(f"{knowledge.RULES_DIR}/{knowledge.PRODUCT}", "# Product\n\nstale\n")
        onboard.write(self.ctx(), force=True)
        self.assertIn(
            "ANSWER THIS", (self.repo / knowledge.RULES_DIR / knowledge.PRODUCT).read_text()
        )

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

    def test_returns_a_path_outside_the_repository(self):
        """A worktree inside the tree shows up in every status, glob and scan."""
        proc = self.hook(branch="feature-x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = proc.stdout.strip()
        self.assertTrue(path)
        self.assertNotIn(str(self.repo.resolve()) + "/", path + "/")

    def test_honours_an_explicit_path(self):
        target = self.tmp / "explicit"
        self.assertEqual(self.hook(path=str(target)).stdout.strip(), str(target.resolve()))

    def test_seeds_trust(self):
        """Without trust, project settings, hooks and the status line silently never run."""
        self.hook(branch="feature-y")
        config = json.loads((self.tmp / ".claude.json").read_text())
        entries = list(config["projects"].values())
        self.assertTrue(any(e.get("hasTrustDialogAccepted") for e in entries))

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
