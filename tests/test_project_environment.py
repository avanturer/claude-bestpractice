"""The Stop gate runs a project's tests in the project's own environment (#282).

A uv project in `backend/` runs its tests with `uv run pytest`, in `backend/.venv`. Driven with
whatever `python3` the PATH found first, every test file failed to import `sqlalchemy`, and the
gate filed two failing of two over a suite the founder's own command passed 33 of 33.
"""

from __future__ import annotations

import subprocess
import venv
from pathlib import Path
from unittest import mock

from helpers import RepoCase

from claude_bestpractice import config, witness

ONLY_IN_THE_VENV = "only_in_the_project_venv"


def make_venv(where: Path) -> Path:
    """A real virtual environment at `where`, seeing the running interpreter's packages, with
    one module of its own that no other interpreter has. Its python, or None without pytest."""
    venv.create(where, system_site_packages=True, with_pip=False, symlinks=True)
    python = where / "bin" / "python"
    found = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    Path(found).mkdir(parents=True, exist_ok=True)
    (Path(found) / f"{ONLY_IN_THE_VENV}.py").write_text("VALUE = 33\n", encoding="utf-8")
    return python


class TestWhichPython(RepoCase):
    def test_a_uv_project_is_run_through_uv_without_rewriting_its_lockfile(self):
        self.write("backend/uv.lock", "version = 1\n")
        with mock.patch.object(witness.shutil, "which", return_value="/usr/bin/uv"):
            python = witness.python_for(self.repo / "backend", self.repo)
        self.assertEqual(python, ["uv", "run", "--frozen", "python"])

    def test_without_uv_installed_the_projects_virtualenv_is_used(self):
        self.write("backend/uv.lock", "version = 1\n")
        self.write("backend/.venv/pyvenv.cfg", "home = /usr/bin\n")
        self.write("backend/.venv/bin/python", "")
        with mock.patch.object(witness.shutil, "which", return_value=None):
            python = witness.python_for(self.repo / "backend", self.repo)
        self.assertEqual(python, [str(self.repo / "backend" / ".venv" / "bin" / "python")])

    def test_a_directory_named_venv_without_pyvenv_cfg_is_not_an_environment(self):
        self.write("backend/.venv/bin/python", "")
        self.assertEqual(witness.python_for(self.repo / "backend", self.repo), ["python3"])

    def test_the_environment_is_looked_for_up_to_the_top_of_the_tree_and_no_further(self):
        """A workspace keeps one `.venv` at its root for every member."""
        self.write(".venv/pyvenv.cfg", "home = /usr/bin\n")
        self.write(".venv/bin/python", "")
        member = self.repo / "packages" / "api"
        member.mkdir(parents=True)
        self.assertEqual(witness.python_for(member, self.repo),
                         [str(self.repo / ".venv" / "bin" / "python")])
        self.assertEqual(witness.python_for(member, member), ["python3"])


class TestTheGateRunsInTheProjectsEnvironment(RepoCase):
    def test_a_suite_that_imports_what_only_its_venv_has_passes(self):
        python = make_venv(self.repo / "backend" / ".venv")
        if subprocess.run([str(python), "-c", "import pytest"], capture_output=True).returncode:
            self.skipTest("the interpreter running this suite gives a new venv no pytest")
        self.write("backend/pyproject.toml", "[tool.pytest.ini_options]\n")
        self.write("backend/tests/test_value.py",
                   f"from {ONLY_IN_THE_VENV} import VALUE\n\n\n"
                   "def test_value():\n    assert VALUE == 33\n")
        self.write(".gitignore", ".venv/\n")
        self.commit("a project whose dependency lives in its own venv")
        seen = witness.run(self.ctx())
        self.assertIsNotNone(seen, "pytest was not driven at all")
        self.assertTrue(seen.passed, seen)
        self.assertEqual(seen.executed, 1)


class TestTheDetectedCommand(RepoCase):
    def test_a_uv_project_is_given_uv_run(self):
        self.write("backend/pyproject.toml", "[tool.pytest.ini_options]\n")
        self.write("backend/uv.lock", "version = 1\n")
        self.write("backend/tests/test_a.py", "def test_a():\n    pass\n")
        with mock.patch.object(config.shutil, "which", return_value="/usr/bin/uv"):
            command = config.detect_test_command(self.repo / "backend")
        self.assertEqual(command, ["uv", "run", "--frozen", "python", "-m", "pytest", "-q"])

    def test_without_uv_it_is_python3_as_before(self):
        self.write("backend/pyproject.toml", "[tool.pytest.ini_options]\n")
        self.write("backend/uv.lock", "version = 1\n")
        self.write("backend/tests/test_a.py", "def test_a():\n    pass\n")
        with mock.patch.object(config.shutil, "which", return_value=None):
            command = config.detect_test_command(self.repo / "backend")
        self.assertEqual(command, ["python3", "-m", "pytest", "-q"])

    def test_a_project_without_a_lockfile_is_unchanged(self):
        self.write("pyproject.toml", "[tool.pytest.ini_options]\n")
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.assertEqual(config.detect_test_command(self.repo), ["python3", "-m", "pytest", "-q"])
