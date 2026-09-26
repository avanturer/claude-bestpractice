"""Shared fixtures — and, before anything else, a sandbox HOME.

Set HERE and not only in `conftest.py` because `make test` runs unittest, which never loads
a conftest: the sandbox appeared to work under pytest while the gate that actually decides
whether a release ships was still writing to whoever ran it. Found by the test that asserts
this, on the first full run after it was written (#121).

Every test module imports this one, so the environment is set before any test executes,
whichever runner is driving.
"""

from __future__ import annotations

import os as _os
import tempfile as _tempfile

if not _os.environ.get("CLAUDE_BP_TEST_HOME"):
    _SANDBOX = _tempfile.mkdtemp(prefix="claude-bestpractice-home-")
    _os.environ["CLAUDE_BP_TEST_HOME"] = _SANDBOX
    _os.environ["HOME"] = _SANDBOX
    _os.environ["USERPROFILE"] = _SANDBOX


import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "plugin" / "lib"
BIN = REPO_ROOT / "plugin" / "bin"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from claude_bestpractice import config as _config  # noqa: E402

# No wait for the founder's word in the hooks the suite runs: each refusal it provokes paid
# five seconds for a word nobody was going to send, about four minutes of `make check`. The
# tests about the wait itself restore it with `real_acceptance_grace` (#232).
_os.environ[_config.GRACE_ENV] = "0"

# And no word with GitHub. The Stop gate asks `gh` whether a branch already has a pull
# request before it demands one (#239), and a `gh` logged in where the suite runs would
# answer about whatever repository a fixture's remote names. With no token and an empty
# config it is logged out, and refuses before it reaches the network. The tests about the
# asking put a `gh` of their own first on PATH.
for _token in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
    _os.environ.pop(_token, None)
_os.environ["GH_CONFIG_DIR"] = _os.path.join(_os.environ["CLAUDE_BP_TEST_HOME"], "gh")


def harness_matches(matcher: str, value: str) -> bool:
    """Whether Claude Code fires a hook with this `matcher` for `value` (a tool's name).

    The rules as the hooks reference states them: "*", "" or none match everything; a
    matcher of only letters, digits, `_`, `-`, spaces, `,` and `|` is a list of exact names;
    anything else is an unanchored regular expression. A test that splits the matcher on `|`
    instead asserts a rule the harness does not apply.
    """
    import re

    if matcher in ("", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_\- ,|]+", matcher):
        return value in {part.strip() for part in re.split(r"[|,]", matcher)}
    return re.search(matcher, value) is not None


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


def add_origin(repo: Path, parent: Path) -> Path:
    """A bare `origin` for `repo`, pushed to and set as its remote HEAD.

    The shape every rule about "the trunk" is written for: `origin/HEAD` resolves, a branch
    can have an upstream, and the clone's own `main` can lag the remote's.
    """
    origin = parent / "origin.git"
    git(["init", "-q", "--bare", "-b", "main", str(origin)], parent)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-q", "-u", "origin", "main"], repo)
    git(["remote", "set-head", "origin", "main"], repo)
    return origin


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
        task = plan.add(ctx, "what this turn is doing", paths=list(paths), done_when="stated")
        plan.claim(ctx, task.id, sid(self.repo, session_id), ctx.branch)
        return task

    def add_worktree(self, name: str) -> Path:
        target = self.tmp / name
        git(["worktree", "add", "-q", "-b", name, str(target)], self.repo)
        return target

    def session_record(self, session_id: str, pid: int | None = None):
        """A session record for this case's repository."""
        return session_record_for(self.ctx(), session_id, pid)

    def hook_decision(self, proc):
        """What a PreToolUse gate decided, or None when it expressed no opinion.

        None and "deny" are different facts and the tests turn on the difference: a gate
        that says nothing leaves the permission layer to decide, which is the correct
        outcome for everything outside the boundaries this plugin publishes.

        Here rather than in each test module because three of them ask the same question of
        the same JSON shape, and the slop gate counts a fourth copy as what it is.
        """
        import json

        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return None
        return payload.get("hookSpecificOutput", {}).get("permissionDecision")

    def hook_reason(self, proc) -> str:
        """The text that decision carried, for the assertions that are about the message."""
        import json

        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return ""
        return payload.get("hookSpecificOutput", {}).get("permissionDecisionReason") or ""

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


def hooks_at_once(name: str, events: list[dict], cwd, env: dict | None = None) -> list[str]:
    """One gate per event, all in the same instant: what each printed.

    The way the harness runs the hooks of one message's parallel calls — every process
    already started and waiting on its stdin, then every event handed over together. Run
    one after another, a race between them cannot happen and a test of it proves nothing.
    """
    gates = [
        subprocess.Popen([sys.executable, str(BIN / name)], cwd=str(cwd), env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
        for _ in events
    ]
    time.sleep(1.5)
    for gate, event in zip(gates, events):
        gate.stdin.write(json.dumps({"cwd": str(cwd), **event}))
        gate.stdin.close()
    said = []
    for gate in gates:
        with gate.stdout:
            said.append(gate.stdout.read())
        gate.wait()
    return said


def real_acceptance_grace(case: unittest.TestCase) -> None:
    """The gates `case` runs wait for the founder's word as long as they do for real.

    For the tests about that wait (#232). Every other test runs its gates with none.
    """
    from unittest import mock

    patched = mock.patch.dict(_os.environ, {_config.GRACE_ENV: str(_config.ACCEPTANCE_GRACE)})
    patched.start()
    case.addCleanup(patched.stop)


def a_gate_underway(name: str, event: dict, cwd) -> subprocess.Popen:
    """A gate the harness has already started and handed its event, and not yet heard from.

    For what can happen WHILE a gate runs: the founder's `+merge` recorded after the merge
    it allows was asked for, and before the gate answered (#232).
    """
    gate = subprocess.Popen([sys.executable, str(BIN / name)], cwd=str(cwd),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    gate.stdin.write(json.dumps({"cwd": str(cwd), **event}))
    gate.stdin.close()
    return gate


def answer_of(gate: subprocess.Popen) -> subprocess.CompletedProcess:
    """What a gate started by `a_gate_underway` finally said: one small JSON on stdout."""
    gate.wait(timeout=60)
    with gate.stdout:
        said = gate.stdout.read()
    return subprocess.CompletedProcess(gate.args, gate.returncode, said, "")


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


def process_gone(pid: int, within: float = 5.0) -> bool:
    """Whether a process has exited within a few seconds. A zombie counts as exited.

    Zombies count because who reaps an orphan is the machine's business, not the gate's:
    in a container whose init never waits, a killed process stays in the table forever.
    """
    import os
    import time

    end = time.time() + within
    while time.time() < end:
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                if handle.read().rsplit(b")", 1)[1].split()[0] == b"Z":
                    return True
        except OSError:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
        time.sleep(0.05)
    return False


def sid(cwd, session_id: str) -> str:
    """The identity a gate will actually register under, for a raw harness id.

    Identity is (harness id, worktree) since four concurrent `claude -p` children were
    found to inherit one `CLAUDE_CODE_SESSION_ID` and collapse into a single, incoherent
    record. A test that looks a session up by the raw id is asking the wrong question.
    """
    sys.path.insert(0, str(REPO_ROOT / "plugin" / "lib"))
    from claude_bestpractice.hookio import HookEvent

    return HookEvent({"session_id": session_id, "cwd": str(cwd)}).session_id
