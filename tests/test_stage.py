"""Stage inference and the ratchet. Stage is computed; it is never a setting."""

from __future__ import annotations

import unittest

from helpers import RepoCase

from founder_os import config, stage


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
        from founder_os import store

        ctx = self.ctx()
        stage.current(ctx)
        self.assertTrue(store.tier_a(ctx, stage.STAGE_FILE).exists())


class TestGates(unittest.TestCase):
    def test_spine_fires_at_every_stage(self):
        """The evidence gate does not scale down. A lying prototype is still lying."""
        for name in (stage.PROTOTYPE, stage.TRACTION, stage.REVENUE):
            gates = stage.gates_for(name)
            self.assertTrue(gates["evidence_gate"], name)
            self.assertTrue(gates["scope_drift"], name)
            self.assertTrue(gates["secret_scan"], name)

    def test_expensive_gates_are_off_at_prototype(self):
        gates = stage.gates_for(stage.PROTOTYPE)
        self.assertFalse(gates["clean_rerun"])
        self.assertFalse(gates["migration_gate"])
        self.assertFalse(gates["triple_run_critical"])

    def test_prototype_turns_a_rule_off_rather_than_on(self):
        """Back-compat shims are banned while nothing consumes the code."""
        self.assertTrue(stage.gates_for(stage.PROTOTYPE)["forbid_compat_shims"])
        self.assertFalse(stage.gates_for(stage.REVENUE)["forbid_compat_shims"])

    def test_revenue_enables_everything_traction_does(self):
        traction = stage.gates_for(stage.TRACTION)
        revenue = stage.gates_for(stage.REVENUE)
        for key, value in traction.items():
            if value and key != "forbid_compat_shims":
                self.assertTrue(revenue[key], key)


class TestConfigDetection(RepoCase):
    def test_detects_pytest_from_a_tests_directory(self):
        (self.repo / "tests").mkdir()
        self.assertEqual(config.detect_test_command(self.repo)[:3], ["python3", "-m", "pytest"])

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
