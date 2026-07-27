"""Half-done work is caught mechanically, and only when this session caused it."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import RepoCase


class TestStubDetection(unittest.TestCase):
    def scan(self, name: str, source: str):
        import tempfile

        from claude_bestpractice import discipline

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / name).write_text(source)
            return discipline.scan(root, [name])

    def kinds(self, name: str, source: str) -> set:
        return {f.kind for f in self.scan(name, source)}

    def test_a_pass_only_function_is_a_stub(self):
        self.assertIn("stub", self.kinds("a.py", "def charge(amount):\n    pass\n"))

    def test_not_implemented_is_caught_in_python(self):
        self.assertIn("not-implemented", self.kinds("a.py", "def f():\n    raise NotImplementedError\n"))

    def test_not_implemented_is_caught_in_typescript(self):
        source = 'export function Checkout() {\n  throw new Error("not implemented");\n}\n'
        self.assertIn("not-implemented", self.kinds("ui.tsx", source))

    def test_not_implemented_is_caught_in_rust_and_go(self):
        self.assertIn("not-implemented", self.kinds("a.rs", "fn f() { todo!() }\n"))
        self.assertIn("not-implemented", self.kinds("a.go", 'func f() { panic("not implemented") }\n'))

    def test_an_untracked_todo_is_caught(self):
        self.assertIn("bare-todo", self.kinds("a.py", "def f():\n    # TODO: handle retries\n    return 1\n"))

    def test_a_tracked_todo_is_allowed(self):
        """`TODO(alice)` names an owner; `TODO: later` names nobody and is never seen again."""
        self.assertNotIn("bare-todo", self.kinds("a.py", "# TODO(alice): after the launch\nx = 1\n"))
        self.assertNotIn("bare-todo", self.kinds("a.ts", "// TODO[PROJ-14] ship this\nconst x = 1;\n"))

    def test_real_work_is_not_flagged(self):
        source = (
            "def total(items):\n"
            '    """Sum, in minor units, because floats lose cents."""\n'
            "    return sum(items)\n"
        )
        self.assertEqual(self.kinds("a.py", source), set())

    def test_an_abstract_method_is_not_a_stub(self):
        source = "import abc\n\n\nclass P:\n    @abstractmethod\n    def f(self):\n        ...\n"
        self.assertEqual(self.kinds("a.py", source), set())

    def test_a_file_that_does_not_parse_does_not_crash(self):
        self.assertIsInstance(self.scan("a.py", "def broken(:\n"), list)


class TestOnlyThisSessionsWork(RepoCase):
    def test_pre_existing_stubs_are_not_blamed_on_this_turn(self):
        """A check that always fires is one the agent learns to route around."""
        from claude_bestpractice import discipline

        self.write("legacy.py", "def old():\n    pass\n")
        self.commit()
        baseline = self.ctx().head

        self.write("legacy.py", "def old():\n    pass\n\n\ndef added():\n    return 1\n")
        found = discipline.introduced(self.ctx(), ["legacy.py"], baseline)
        self.assertEqual(found, [], f"blamed a pre-existing stub: {[str(f) for f in found]}")

    def test_a_new_stub_beside_an_old_one_is_caught(self):
        from claude_bestpractice import discipline

        self.write("legacy.py", "def old():\n    pass\n")
        self.commit()
        baseline = self.ctx().head

        self.write("legacy.py", "def old():\n    pass\n\n\ndef fresh():\n    pass\n")
        found = discipline.introduced(self.ctx(), ["legacy.py"], baseline)
        self.assertEqual(len(found), 1, [str(f) for f in found])
        self.assertIn("fresh", found[0].text)

    def test_a_brand_new_file_is_all_this_session_s_doing(self):
        from claude_bestpractice import discipline

        self.write("new.py", "def f():\n    pass\n")
        found = discipline.introduced(self.ctx(), ["new.py"], self.ctx().head)
        self.assertEqual(len(found), 1)


class TestTheGateRefuses(RepoCase):
    def stop(self):
        return self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
        )

    def test_finishing_with_a_stub_is_refused(self):
        self.write("api.py", "def existing():\n    return 1\n")
        self.commit()
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.write("api.py", "def existing():\n    return 1\n\n\ndef charge(amount):\n    pass\n")
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Unfinished work", proc.stderr)
        self.assertIn("charge", proc.stderr)

    def test_it_can_be_switched_off(self):
        self.configure(block_unfinished_work=False)
        self.write("api.py", "def charge(amount):\n    pass\n")
        self.commit()
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.write("api.py", "def charge(amount):\n    pass\n\n\ndef more():\n    pass\n")
        self.assertNotIn("Unfinished work", self.stop().stderr)


class TestAutonomyMode(RepoCase):
    def test_vibecode_is_the_default_and_is_injected(self):
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("mode: vibecode", body)
        self.assertIn("never diffs", body)

    def test_pair_mode_changes_the_line(self):
        self.configure(autonomy="pair")
        proc = self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("mode: pair", body)

    def test_an_unknown_mode_falls_back_and_complains(self):
        from claude_bestpractice import config

        self.configure(autonomy="telepathy")
        cfg, complaints = config.load_checked(self.ctx())
        self.assertEqual(cfg.autonomy, "vibecode")
        self.assertTrue(any("autonomy" in c for c in complaints))


if __name__ == "__main__":
    unittest.main()
