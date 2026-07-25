"""Shared fixtures. Every test gets a real git repository, never a mock.

The substrate is defined in terms of git's own behaviour — the common directory, the
worktree list, blob hashes. A mocked git would test our idea of git rather than git,
and the whole point of the design is that the merge and worktree semantics are real.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "plugin" / "lib"
BIN = REPO_ROOT / "plugin" / "bin"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout.strip()


def make_repo(parent: Path, name: str = "repo", seed: bool = True) -> Path:
    repo = parent / name
    repo.mkdir(parents=True)
    git(["init", "-q", "-b", "main"], repo)
    git(["config", "user.email", "test@founder-os"], repo)
    git(["config", "user.name", "test"], repo)
    # Never sign fixture commits: signing reaches outside the test, and a host
    # signing helper that is unavailable would fail every repository-backed test
    # for a reason that has nothing to do with the code under test.
    git(["config", "commit.gpgsign", "false"], repo)
    git(["config", "tag.gpgsign", "false"], repo)
    if seed:
        (repo / "README.md").write_text("seed\n")
        git(["add", "-A"], repo)
        git(["commit", "-qm", "seed"], repo)
    return repo


class RepoCase(unittest.TestCase):
    """Base class providing a throwaway repository per test."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="founder-os-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def ctx(self):
        from founder_os.gitctx import resolve

        return resolve(self.repo)

    def write(self, relpath: str, content: str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def commit(self, message: str = "change") -> str:
        git(["add", "-A"], self.repo)
        git(["commit", "-qm", message], self.repo)
        return git(["rev-parse", "HEAD"], self.repo)

    def add_worktree(self, name: str) -> Path:
        target = self.tmp / name
        git(["worktree", "add", "-q", "-b", name, str(target)], self.repo)
        return target

    def session_record(self, session_id: str, pid: int | None = None):
        """A session record for this case's repository."""
        return session_record_for(self.ctx(), session_id, pid)

    def run_hook(self, name: str, event: dict, env: dict | None = None):
        """Invoke a gate exactly as the harness does: executable, event JSON on stdin."""
        import json
        import subprocess
        import sys

        return subprocess.run(
            [sys.executable, str(BIN / name)],
            input=json.dumps({"cwd": str(self.repo), **event}),
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            timeout=180,
            env=env,
        )


def session_record_for(ctx, session_id: str, pid: int | None = None):
    """Build a session record for an arbitrary context.

    Free function rather than a method because several tests need a record for a
    SIBLING worktree's context, which is the whole point of the coordination layer.
    """
    import os
    import time

    from founder_os import sessions

    now = time.time()
    return sessions.SessionRecord(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid(),
        worktree=ctx.worktree_root.as_posix(),
        branch=ctx.branch,
        baseline_commit=ctx.head,
        started_at=now,
        heartbeat_at=now,
    )
