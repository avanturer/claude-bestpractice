"""Half-done work is caught mechanically, and only when this session caused it."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import RepoCase, add_origin, another_clone, git, push_from


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

    def test_a_file_that_warns_is_read_without_printing_the_warning(self):
        """The Stop hook's stderr is what the founder reads under a refusal, and it carried
        `<unknown>:352: SyntaxWarning: invalid escape sequence` about a file of theirs that
        the refusal never named (#236). The stub in that file is still found."""
        import warnings

        source = 'PATTERN = "' + chr(92) + 's+"\n\ndef charge(amount):\n    pass\n'
        with warnings.catch_warnings(record=True) as printed:
            warnings.simplefilter("always")
            kinds = self.kinds("a.py", source)
        self.assertEqual([], [str(w.message) for w in printed])
        self.assertIn("stub", kinds)

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

    def test_an_abstract_method_spelled_pass_is_not_a_stub(self):
        source = "import abc\n\n\nclass P(abc.ABC):\n    @abc.abstractmethod\n    def f(self):\n        pass\n"
        self.assertEqual(self.kinds("a.py", source), set())

    def test_a_protocol_method_with_only_a_docstring_is_not_a_stub(self):
        """#258: the interface from the report, as it was written. Its method blocked a finish
        four times as `empty-function — exists() has no body`, and then the merge."""
        source = (
            "from typing import Protocol\n\n\n"
            "class PhotoStorage(Protocol):\n"
            "    def exists(self, key: str) -> bool:\n"
            '        """Whether a photo is stored under this key."""\n\n'
            "    async def save(self, key: str, data: bytes) -> None:\n"
            "        ...\n"
        )
        self.assertEqual(self.kinds("photo_storage.py", source), set())

    def test_a_protocol_is_recognised_however_it_is_spelled(self):
        for base in ("typing.Protocol", "Protocol[T]", "typing_extensions.Protocol", "Base, Protocol"):
            with self.subTest(base=base):
                source = f"class Store({base}):\n    def get(self, key):\n        ...\n"
                self.assertEqual(self.kinds("a.py", source), set())

    def test_a_class_implementing_a_protocol_is_still_checked(self):
        """Only the protocol's own methods are its interface. A class that matches it is the
        implementation, and an empty method there is the unfinished work this gate is for."""
        source = (
            "from typing import Protocol\n\n\n"
            "class Store(Protocol):\n"
            "    def get(self, key): ...\n\n\n"
            "class DiskStore(Store):\n"
            "    def get(self, key):\n"
            '        """Read it from disk."""\n\n\n'
            "class CloudStore:\n"
            "    def get(self, key):\n"
            "        pass\n"
        )
        found = {(f.kind, f.text) for f in self.scan("a.py", source)}
        self.assertEqual(found, {("empty-function", "get() has no body"), ("stub", "get() is `pass`")})

    def test_overload_signatures_are_not_stubs(self):
        source = (
            "import typing\nfrom typing import overload\n\n\n"
            "@overload\ndef parse(raw: str) -> int: ...\n\n\n"
            "@typing.overload\ndef parse(raw: bytes) -> int: ...\n\n\n"
            "def parse(raw):\n    return int(raw)\n"
        )
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

    def test_a_stub_that_came_with_a_fast_forward_is_upstreams(self):
        """#255: measured against the baseline alone, a stub merged upstream and pulled into
        a file this session then edited was "introduced in this turn"."""
        from claude_bestpractice import discipline

        self.write("legacy.py", "def old():\n    return 1\n")
        self.commit()
        baseline = self.ctx().head
        self.write("legacy.py", "def old():\n    return 1\n\n\ndef theirs():\n    pass\n")
        self.commit("somebody else's, merged and pulled")
        floor = self.ctx().head

        self.write("legacy.py", "def old():\n    return 2\n\n\ndef theirs():\n    pass\n")
        self.assertEqual([], discipline.introduced(self.ctx(), ["legacy.py"], baseline, floor))
        self.assertEqual(1, len(discipline.introduced(self.ctx(), ["legacy.py"], baseline)))


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

    def test_a_protocol_written_this_turn_is_not_unfinished_work(self):
        """#258 end to end: the interface was refused as a stub at Stop, and the finish it
        marked UNVERIFIED then held up the merge."""
        self.write("api.py", "def existing():\n    return 1\n")
        self.commit()
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        self.write("api.py", (
            "from typing import Protocol\n\n\n"
            "def existing():\n    return 1\n\n\n"
            "class PhotoStorage(Protocol):\n"
            "    def exists(self, key: str) -> bool:\n"
            '        """Whether a photo is stored under this key."""\n'
        ))
        stderr = self.stop().stderr
        self.assertNotIn("Unfinished work", stderr)
        self.assertNotIn("exists()", stderr)

    def test_a_stub_that_arrived_on_another_pull_requests_branch_is_not_this_turns(self):
        """#258's other half end to end: the file came with a fast-forward onto another pull
        request's branch, and the gate refused it as written in this turn."""
        self.configure(manage_pull_requests=False, require_task=False)
        self.commit("the founder's settings")
        theirs = another_clone(add_origin(self.repo, self.tmp), self.tmp / "theirs")
        push_from(theirs, "feat/theirs", "photo_storage.py",
                  "class PhotoStorage:\n    def exists(self, key):\n        pass\n")

        git(["checkout", "-q", "-b", "feat/mine"], self.repo)
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})
        git(["fetch", "-q", "origin"], self.repo)
        git(["merge", "-q", "--ff-only", "origin/feat/theirs"], self.repo)

        proc = self.stop()
        self.assertNotIn("Unfinished work", proc.stderr)
        self.assertEqual(0, proc.returncode, proc.stderr)

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
