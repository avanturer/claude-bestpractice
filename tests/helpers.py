"""Shared fixtures. Every test gets a real git repository, never a mock.

The substrate is defined in terms of git's own behaviour — the common directory, the
worktree list, blob hashes. A mocked git would test our idea of git rather than git,
and the whole point of the design is that the merge and worktree semantics are real.
"""

from __future__ import annotations

import json
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


def make_repo(parent: Path, name: str = "repo", seed: bool = True, relax_git_policy: bool = False) -> Path:
    repo = parent / name
    repo.mkdir(parents=True)
    git(["init", "-q", "-b", "main"], repo)
    git(["config", "user.email", "test@claude-bestpractice"], repo)
    git(["config", "user.name", "test"], repo)
    # Never sign fixture commits: signing reaches outside the test, and a host
    # signing helper that is unavailable would fail every repository-backed test
    # for a reason that has nothing to do with the code under test.
    git(["config", "commit.gpgsign", "false"], repo)
    git(["config", "tag.gpgsign", "false"], repo)
    if seed:
        (repo / "README.md").write_text("seed\n")
        if relax_git_policy:
            # Committed, not just written: config is Tier A by design, and leaving it
            # untracked would make every "the tree is clean" assertion false.
            cfg = repo / ".claude" / "claude-bestpractice" / "config.json"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(json.dumps({"require_worktree": False, "protect_trunk": False}))
        git(["add", "-A"], repo)
        git(["commit", "-qm", "seed"], repo)
    return repo


class RepoCase(unittest.TestCase):
    """Base class providing a throwaway repository per test."""

    # The fixture repository is a main checkout on the trunk, which the git policy
    # refuses by default — correctly, since that is the state where parallel sessions
    # silently overwrite each other. Every test that is not ABOUT that rule opts out
    # here; `test_gitpolicy.py` opts back in and is where the default is proven.
    relax_git_policy = True

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="claude-bestpractice-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp, relax_git_policy=self.relax_git_policy)

    def configure(self, **values) -> None:
        """Merge keys into this repository's claude-bestpractice config."""
        import json

        path = self.repo / ".claude" / "claude-bestpractice" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        current = {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                current = {}
        current.update(values)
        path.write_text(json.dumps(current), encoding="utf-8")

    def ctx(self):
        from claude_bestpractice.gitctx import resolve

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

    def claim_a_task(self, session_id: str = "s1", *paths: str):
        """Put a turn's work on the board, which the Stop gate requires it to be on.

        The identity is the COMPOSED one — (harness id, worktree) — because that is what
        every gate reads. A task owned by the raw id is owned by somebody the registry has
        never heard of, which is the defect this helper exists not to reproduce.
        """
        from claude_bestpractice import plan

        ctx = self.ctx()
        task = plan.add(ctx, "what this turn is doing", paths=list(paths))
        plan.claim(ctx, task.id, sid(self.repo, session_id), ctx.branch)
        return task

    def add_worktree(self, name: str) -> Path:
        target = self.tmp / name
        git(["worktree", "add", "-q", "-b", name, str(target)], self.repo)
        return target

    def session_record(self, session_id: str, pid: int | None = None):
        """A session record for this case's repository."""
        return session_record_for(self.ctx(), session_id, pid)

    def run_hook(self, name: str, event: dict, env: dict | None = None, cwd=None):
        """Invoke a gate exactly as the harness does: executable, event JSON on stdin.

        `cwd` overrides the repository so a test can fire a gate from a worktree or from
        a different repository — the worktree rules are only testable that way.
        """
        import json
        import subprocess
        import sys

        where = cwd or self.repo
        return subprocess.run(
            [sys.executable, str(BIN / name)],
            input=json.dumps({"cwd": str(where), **event}),
            capture_output=True,
            text=True,
            cwd=str(where),
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

    from claude_bestpractice import sessions

    now = time.time()
    return sessions.SessionRecord(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid(),
        # A pid is only evidence of life or death when it was resolved to the CLI itself.
        # Tests that hand in a dead pid are asserting about a session whose owner died,
        # so they have to say the pid was the owner's — the default, an unresolved pid,
        # deliberately proves nothing. This is the distinction the fleet-wide invisibility
        # bug hid behind: under test the hook's parent is the test runner, which stays
        # alive, so watching the wrong process looked exactly like watching the right one.
        pid_trust=sessions.PID_TRUST_OWNER,
        worktree=ctx.worktree_root.as_posix(),
        branch=ctx.branch,
        baseline_commit=ctx.head,
        started_at=now,
        heartbeat_at=now,
    )


def sid(cwd, session_id: str) -> str:
    """The identity a gate will actually register under, for a raw harness id.

    Identity is (harness id, worktree) since four concurrent `claude -p` children were
    found to inherit one `CLAUDE_CODE_SESSION_ID` and collapse into a single, incoherent
    record. A test that looks a session up by the raw id is asking the wrong question.
    """
    sys.path.insert(0, str(REPO_ROOT / "plugin" / "lib"))
    from claude_bestpractice.hookio import HookEvent

    return HookEvent({"session_id": session_id, "cwd": str(cwd)}).session_id
