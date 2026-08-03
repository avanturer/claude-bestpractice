"""Stage inference and the ratchet. Stage is computed; it is never a setting."""

from __future__ import annotations

import subprocess
import unittest

from helpers import BIN, LIB, RepoCase, git

from claude_bestpractice import config, stage


class TestClassification(RepoCase):
    def test_bare_repo_is_prototype(self):
        resolved, _ = stage.current(self.ctx())
        self.assertEqual(resolved, stage.PROTOTYPE)

    def test_ci_plus_deploy_reaches_traction(self):
        self.write(".github/workflows/ci.yml", "on: push\n")
        self.write("fly.toml", "app = 'x'\n")
        resolved, signals = stage.current(self.ctx())
        self.assertEqual(resolved, stage.TRACTION)
        self.assertTrue(signals.has_ci and signals.has_deploy_config)

    def test_ci_alone_is_still_prototype(self):
        """A test workflow is not a product. One signal must not promote."""
        self.write(".github/workflows/ci.yml", "on: push\n")
        resolved, _ = stage.current(self.ctx())
        self.assertEqual(resolved, stage.PROTOTYPE)

    def test_user_table_migration_reaches_traction(self):
        self.write("migrations/0001_init.sql", "CREATE TABLE users (id serial primary key);")
        resolved, signals = stage.current(self.ctx())
        self.assertEqual(resolved, stage.TRACTION)
        self.assertTrue(signals.has_user_table)

    def test_auth_dependency_reaches_traction(self):
        self.write("package.json", '{"dependencies": {"next-auth": "^4.0.0"}}')
        resolved, _ = stage.current(self.ctx())
        self.assertEqual(resolved, stage.TRACTION)

    def test_payment_dependency_reaches_revenue(self):
        self.write("package.json", '{"dependencies": {"stripe": "^14.0.0"}}')
        resolved, signals = stage.current(self.ctx())
        self.assertEqual(resolved, stage.REVENUE)
        self.assertTrue(signals.has_payment_dep)

    def test_live_key_shape_in_committed_config_reaches_revenue(self):
        self.write("fly.toml", 'STRIPE = "sk_live_abcdefghijklmnop"\n')
        resolved, _ = stage.current(self.ctx())
        self.assertEqual(resolved, stage.REVENUE)


class TestRatchet(RepoCase):
    def test_stage_never_regresses_when_a_signal_disappears(self):
        """Deleting a CI file must not silently switch off the gates it enabled."""
        self.write("package.json", '{"dependencies": {"stripe": "^14.0.0"}}')
        first, _ = stage.current(self.ctx())
        self.assertEqual(first, stage.REVENUE)

        (self.repo / "package.json").unlink()
        second, signals = stage.current(self.ctx())
        self.assertEqual(second, stage.REVENUE)
        self.assertTrue(any("ratchet held" in r for r in signals.reasons))

    def test_override_can_raise_but_not_lower(self):
        self.write("package.json", '{"dependencies": {"stripe": "^14.0.0"}}')
        resolved, _ = stage.current(self.ctx(), override=stage.PROTOTYPE)
        self.assertEqual(resolved, stage.REVENUE)

    def test_override_raises_a_prototype(self):
        resolved, _ = stage.current(self.ctx(), override=stage.REVENUE)
        self.assertEqual(resolved, stage.REVENUE)

    def test_stage_is_recorded_in_tier_a(self):
        from claude_bestpractice import store

        ctx = self.ctx()
        stage.current(ctx)
        self.assertTrue(store.tier_a(ctx, stage.STAGE_DIR, "reached-prototype.json").exists())

    def test_the_committed_marker_holds_nothing_that_varies(self):
        """A timestamp or a signal dump in here is what made merges conflict."""
        from claude_bestpractice import store

        ctx = self.ctx()
        stage.current(ctx)
        body = store.read_json(store.tier_a(ctx, stage.STAGE_DIR, "reached-prototype.json"))
        self.assertEqual(body, {"stage": stage.PROTOTYPE})

    def test_the_volatile_half_is_not_committed(self):
        from claude_bestpractice import store

        ctx = self.ctx()
        stage.current(ctx)
        recorded = store.read_json(store.tier_b(ctx, stage.SIGNALS_FILE), default={})
        self.assertIn("signals", recorded)

        # Not "outside the repo directory" — Tier B is under .git/, which is inside it.
        # The property that matters is that git cannot see it, so ask git.
        untracked = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(self.repo), capture_output=True, text=True, timeout=30,
        ).stdout
        self.assertNotIn(stage.SIGNALS_FILE, untracked)

    def test_a_marker_alone_holds_the_ratchet(self):
        """The committed marker is the whole record; nothing else needs to survive."""
        from claude_bestpractice import store

        ctx = self.ctx()
        store.write_json(store.tier_a(ctx, stage.STAGE_DIR, "reached-revenue.json"),
                         {"stage": stage.REVENUE})
        resolved, signals = stage.current(ctx)
        self.assertEqual(resolved, stage.REVENUE)
        self.assertTrue(any("ratchet held" in r for r in signals.reasons))

    def test_two_branches_at_the_same_stage_merge_without_a_conflict(self):
        """The reason the shape changed, asserted against real git rather than reasoning.

        Two sessions that each merely ran a gate — no stage change, nothing deliberate —
        used to come back with a conflict in a file the founder has never heard of.
        """
        ctx = self.ctx()
        stage.current(ctx)
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "base"], self.repo)

        git(["switch", "-qc", "feat/a"], self.repo)
        self.write("a.txt", "a\n")
        stage.current(self.ctx())
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "a"], self.repo)

        git(["switch", "-q", "main"], self.repo)
        self.write("b.txt", "b\n")
        stage.current(self.ctx())
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", "b"], self.repo)

        merged = subprocess.run(
            ["git", "merge", "--no-edit", "feat/a"],
            cwd=str(self.repo), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)


class TestGates(unittest.TestCase):
    def test_spine_fires_at_every_stage(self):
        """The evidence gate does not scale down. A lying prototype is still lying."""
        for name in (stage.PROTOTYPE, stage.TRACTION, stage.REVENUE):
            gates = stage.gates_for(name)
            self.assertTrue(gates["evidence_gate"], name)
            self.assertTrue(gates["scope_drift"], name)

    def test_expensive_gates_are_off_at_prototype(self):
        gates = stage.gates_for(stage.PROTOTYPE)
        self.assertFalse(gates["clean_rerun"])
        self.assertFalse(gates["migration_gate"])

    def test_revenue_enables_everything_traction_does(self):
        traction = stage.gates_for(stage.TRACTION)
        revenue = stage.gates_for(stage.REVENUE)
        for key, value in traction.items():
            if value:
                self.assertTrue(revenue[key], key)

    def test_no_gate_switches_off_as_the_stage_rises(self):
        """The table is a ratchet too, not just the stage it is keyed on.

        A gate that fires at prototype and stops at revenue would mean the founder's
        most valuable repository is the least protected one. Nothing in the table may
        be shaped that way, so assert the shape rather than any one row of it.
        """
        rows = [stage.gates_for(name) for name in (stage.PROTOTYPE, stage.TRACTION, stage.REVENUE)]
        for lower, higher in zip(rows, rows[1:]):
            for key, on in lower.items():
                if on:
                    self.assertTrue(higher[key], key)

    def test_every_declared_gate_has_a_consumer(self):
        """A flag no code reads is a claim, and this project's thesis forbids claims.

        `forbid_compat_shims` sat in this table for weeks, promised in three READMEs and
        read by nothing. It passed every test because the tests asserted the flag's value
        rather than its effect.
        """
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in list((LIB / "claude_bestpractice").rglob("*.py")) + list(BIN.iterdir())
            if path.is_file() and path.name != "stage.py"
        )
        for key in stage.gates_for(stage.REVENUE):
            self.assertIn(key, source, f"{key} is declared in stage.gates_for and read nowhere")


class TestConfigDetection(RepoCase):
    def test_detects_pytest_from_python_test_files(self):
        """A file, not a directory name — this assertion used to encode the defect.

        It asserted that an empty directory called `tests` meant pytest, which is what
        baked `python3 -m pytest -q` into Jekyll's pre-push hook and refused every push
        out of a Ruby repository.
        """
        self.write("tests/test_thing.py", "def test_x():\n    assert True\n")
        self.assertEqual(config.detect_test_command(self.repo)[:3], ["python3", "-m", "pytest"])

    def test_an_empty_tests_directory_is_not_evidence_of_python(self):
        (self.repo / "tests").mkdir()
        self.assertEqual([], config.detect_test_command(self.repo))

    def test_detects_cargo(self):
        self.write("Cargo.toml", "[package]\nname='x'\n")
        self.assertEqual(config.detect_test_command(self.repo)[0], "cargo")

    def test_ignores_a_package_json_without_a_real_test_script(self):
        self.write("package.json", '{"scripts": {"test": "echo \\"Error: no test specified\\""}}')
        self.assertEqual(config.detect_test_command(self.repo), [])

    def test_accepts_a_real_npm_test_script(self):
        self.write("package.json", '{"scripts": {"test": "vitest run"}}')
        self.assertEqual(config.detect_test_command(self.repo)[0], "npm")

    def test_no_signal_yields_no_command(self):
        self.assertEqual(config.detect_test_command(self.repo), [])

    def test_config_roundtrip_through_tier_a(self):
        ctx = self.ctx()
        cfg = config.load(ctx)
        cfg.max_tool_calls = 42
        config.save(ctx, cfg)
        self.assertEqual(config.load(ctx).max_tool_calls, 42)


if __name__ == "__main__":
    unittest.main()
