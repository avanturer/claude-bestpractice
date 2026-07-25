"""The LLM-first documentation gate."""

from __future__ import annotations

import subprocess
import sys
import unittest

from helpers import REPO_ROOT, RepoCase, git

TOOL = REPO_ROOT / "tools" / "check_docstrings.py"


class ToolCase(RepoCase):
    """The gate diffs against git, so each case needs its own repository."""

    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=120,
            env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )


class TestSignatureLogic(unittest.TestCase):
    """Signature extraction, unit-level."""

    def setUp(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        import importlib

        self.mod = importlib.import_module("check_docstrings")

    def sig(self, source: str) -> str:
        import ast

        tree = ast.parse(source)
        return self.mod.signature_of(tree.body[0])

    def test_captures_names_annotations_and_return(self):
        self.assertEqual(self.sig("def f(a: int, b: str) -> bool: ..."), "(a:int,b:str)->bool")

    def test_renaming_a_parameter_changes_the_signature(self):
        self.assertNotEqual(self.sig("def f(a: int): ..."), self.sig("def f(b: int): ..."))

    def test_changing_a_default_does_not(self):
        """Otherwise every tuning tweak demands a docstring edit, which teaches filler."""
        self.assertEqual(self.sig("def f(a: int = 1): ..."), self.sig("def f(a: int = 2): ..."))

    def test_missing_annotation_is_recorded_as_unknown(self):
        self.assertEqual(self.sig("def f(a): ..."), "(a:?)->?")

    def test_varargs_are_included(self):
        self.assertIn("*args", self.sig("def f(*args: int) -> None: ..."))


class TestDerivableForms(ToolCase):
    def test_flags_google_style_args_block(self):
        self.write(
            "mod.py",
            'def f(a: int) -> int:\n    """Do a thing.\n\n    Args:\n        a: the thing\n    """\n    return a\n',
        )
        out = self.run_tool("--all")
        self.assertEqual(out.returncode, 1)
        self.assertIn("restates its signature", out.stdout)

    def test_flags_sphinx_style(self):
        self.write("mod.py", 'def f(a: int) -> int:\n    """Thing.\n\n    :param a: the thing\n    """\n    return a\n')
        self.assertEqual(self.run_tool("--all").returncode, 1)

    def test_accepts_a_docstring_carrying_non_derivable_information(self):
        self.write(
            "mod.py",
            'def f(a: int) -> int:\n    """Raises on negatives: two callers relied on clamping and both were wrong."""\n    return a\n',
        )
        out = self.run_tool("--all")
        self.assertEqual(out.returncode, 0, out.stdout)

    def test_flags_an_init_docstring(self):
        self.write("mod.py", 'class C:\n    def __init__(self) -> None:\n        """Build a C."""\n')
        self.assertEqual(self.run_tool("--all").returncode, 1)


class TestCommentScanning(ToolCase):
    def test_flags_narration(self):
        self.write("mod.py", "def f() -> None:\n    # Step 1: get the thing\n    pass\n")
        out = self.run_tool("--all")
        self.assertEqual(out.returncode, 1)
        self.assertIn("narration", out.stdout)

    def test_flags_commented_out_code(self):
        self.write("mod.py", "# def old() -> None:\n#     pass\ndef f() -> None:\n    pass\n")
        out = self.run_tool("--all")
        self.assertEqual(out.returncode, 1)
        self.assertIn("commented-out", out.stdout)

    def test_flags_a_bare_todo(self):
        self.write("mod.py", "def f() -> None:\n    # TODO: make it faster\n    pass\n")
        self.assertEqual(self.run_tool("--all").returncode, 1)

    def test_accepts_a_qualified_todo(self):
        self.write(
            "mod.py",
            "def f() -> None:\n    # TODO(perf): batch these once /v2/ingest ships.\n    pass\n",
        )
        self.assertEqual(self.run_tool("--all").returncode, 0)

    def test_does_not_flag_comment_text_inside_a_string(self):
        """A comment checker that fires on string literals gets disabled."""
        self.write("mod.py", 'SAMPLE = "# TODO: rename this"\nOTHER = "# Step 1: go"\n')
        out = self.run_tool("--all")
        self.assertEqual(out.returncode, 0, out.stdout)


class TestSignatureDrift(ToolCase):
    def test_flags_a_changed_signature_with_an_unchanged_docstring(self):
        self.write(
            "mod.py",
            'def f(a: int) -> int:\n    """Clamps to zero, because the caller cannot handle negatives."""\n    return a\n',
        )
        self.commit()
        self.write(
            "mod.py",
            'def f(a: int, b: str) -> bool:\n    """Clamps to zero, because the caller cannot handle negatives."""\n    return True\n',
        )
        out = self.run_tool()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("changed signature but its docstring did not", out.stdout)

    def test_accepts_when_both_changed(self):
        self.write("mod.py", 'def f(a: int) -> int:\n    """Old reason."""\n    return a\n')
        self.commit()
        self.write("mod.py", 'def f(a: int, b: str) -> bool:\n    """New reason entirely."""\n    return True\n')
        self.assertEqual(self.run_tool().returncode, 0)

    def test_ignores_a_function_with_no_docstring(self):
        self.write("mod.py", "def f(a: int) -> int:\n    return a\n")
        self.commit()
        self.write("mod.py", "def f(a: int, b: str) -> bool:\n    return True\n")
        self.assertEqual(self.run_tool().returncode, 0)

    def test_ignores_a_new_file(self):
        self.write("fresh.py", 'def f(a: int) -> int:\n    """Reason."""\n    return a\n')
        self.assertEqual(self.run_tool().returncode, 0)

    def test_body_change_alone_is_not_drift(self):
        """Refactoring an implementation must not demand a docstring edit."""
        self.write("mod.py", 'def f(a: int) -> int:\n    """Reason."""\n    return a + 0\n')
        self.commit()
        self.write("mod.py", 'def f(a: int) -> int:\n    """Reason."""\n    return a\n')
        self.assertEqual(self.run_tool().returncode, 0)


class TestSelfCheck(unittest.TestCase):
    def test_this_project_passes_its_own_gate(self):
        """A standard the authors exempt themselves from is decoration."""
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--all"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)


if __name__ == "__main__":
    unittest.main()
