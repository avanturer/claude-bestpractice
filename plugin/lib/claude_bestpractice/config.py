"""Configuration: detected, not asked for.

Every value here has a working default derived from the repository. A founder who
never writes a config file gets correct behaviour; the file exists only to override a
detection that got it wrong.

Config lives in Tier A (committed) so all worktrees and all sessions agree. Eight
sessions reading different settings is the contradictory-instruction failure this
plugin exists to prevent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import store
from .gitctx import GitContext

CONFIG_NAME = "config.json"

# Ordered by specificity: the first runner whose marker file exists wins.
_TEST_RUNNERS: list[tuple[str, str, list[str]]] = [
    ("pytest.ini", "pytest", ["python3", "-m", "pytest", "-q"]),
    ("pyproject.toml", "pytest", ["python3", "-m", "pytest", "-q"]),
    ("tox.ini", "pytest", ["python3", "-m", "pytest", "-q"]),
    ("Cargo.toml", "cargo", ["cargo", "test", "--quiet"]),
    ("go.mod", "go", ["go", "test", "./..."]),
    ("package.json", "npm", ["npm", "test", "--silent"]),
    ("Makefile", "make", ["make", "test"]),
]

# Where a machine-readable result lands. Checked in order; the newest match wins.
DEFAULT_ARTIFACT_GLOBS = [
    "junit.xml",
    "report.xml",
    "test-results.xml",
    "pytest-report.json",
    "test-results/**/*.xml",
    "reports/**/*.xml",
    "target/nextest/**/*.xml",
]


# How much the founder is in the loop. Advisory by construction — no matcher can tell
# "asked a necessary question" from "asked because asking is easier than finding out" —
# so this is injected as context rather than enforced, and it is labelled as such.
#
#   vibecode  the agent does everything it can do itself: finds credentials in the
#             environment, reads the PR, searches the web, decides. The founder is shown
#             outcomes — numbers, a preview, a working feature — and never the diff.
#   pair      the agent proposes before acting on anything structural.
AUTONOMY = ("vibecode", "pair")

@dataclass
class Config:
    test_command: list[str] = field(default_factory=list)
    artifact_globs: list[str] = field(default_factory=lambda: list(DEFAULT_ARTIFACT_GLOBS))
    # Paths the gate's own run skips. Lives HERE, not in pytest.ini: this file is refused
    # to the session by `pre-tool`, so the list is the founder's, while `addopts` in a
    # runner config is one line the gated party can write (#158).
    witness_exclude: list[str] = field(default_factory=list)
    # No two live sessions on one database. Worktrees isolate files and nothing else, so
    # one session's open transaction blocks every sibling's tests on its locks (#164).
    isolate_databases: bool = True
    # How THIS project brings a database into existence. Empty for the many stacks whose
    # migrations create it on first use. Cannot be guessed: `createdb` is Postgres, and a
    # plugin that hardcodes it breaks on the first repository that is not.
    worktree_setup: list[str] = field(default_factory=list)
    clean_rerun: bool | None = None
    scope_drift_block: bool = True
    loop_detect: bool = True
    leases_enabled: bool = True
    lease_ttl_seconds: float = 1800.0
    # Off. A ceiling on tool calls catches DURATION, and a runaway is a SHAPE — the two
    # detectors that read shape, `max_repeat_signature` and `loop_detect`, are what
    # actually stop one. By count alone an eleven-hour measuring session is indistinguishable
    # from a loop, so the ceiling only ever fired on the wrong one, and when it fired it
    # refused everything including the read that would have shown the result.
    #
    # Kept as a key rather than deleted: somebody may want a ceiling, and a number they
    # chose is a different thing from a number this plugin invented. Any value above zero
    # enforces again.
    max_tool_calls: int = 0
    max_repeat_signature: int = 3
    require_worktree: bool = True
    # Hours a claimed task may sit untouched before it goes back to the queue. The board's
    # whole claim is that it says what is in flight; a row nobody is working on is that
    # claim being false. Zero switches the sweep off.
    task_idle_hours: float = 24.0
    task_queue_stale_days: float = 21.0
    # Work that changed files while the ledger says nothing is in flight. Same shape as
    # every other Stop demand: satisfied once per session, then never seen again.
    require_task: bool = True
    block_unfinished_work: bool = True
    compare_dependencies: bool = True
    commit_conventions: bool = True
    autonomy: str = "vibecode"
    protect_trunk: bool = True
    manage_pull_requests: bool = True
    # "local" captures and holds, "auto" also files, "off" does not capture. Not `auto`
    # by default: filing uses the installer's own credentials and posts publicly under
    # their name in a repository they do not own.
    report_defects: str = "local"
    stage_override: str | None = None
    # Test directories are exempt because THIS PLUGIN demands the test. Without them the
    # scope-drift check and the evidence gate deadlock on the most ordinary task there is:
    # "fix the discount handling in src/billing.py" — the agent fixes it, writes the test
    # the Stop gate requires, and is blocked for touching a file the task did not name.
    # Four blocks, then an UNVERIFIED finish, then a permanent `outcome: failed` attempt
    # filed against work that was correct, tested and green. On a first task, unprompted.
    #
    # An earlier round exempted the ARTIFACT (junit.xml) and not the test SOURCE that
    # produces it, which is why the README already boasts of fixing this deadlock while
    # the deadlock was still there.
    exempt_paths: list[str] = field(
        default_factory=lambda: [
            ".claude/", "docs/", "README.md", "CHANGELOG.md",
            "tests/", "test/", "spec/", "__tests__/",
        ]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_command": self.test_command,
            "artifact_globs": self.artifact_globs,
            "witness_exclude": self.witness_exclude,
            "isolate_databases": self.isolate_databases,
            "worktree_setup": self.worktree_setup,
            "clean_rerun": self.clean_rerun,
            "scope_drift_block": self.scope_drift_block,
            "loop_detect": self.loop_detect,
            "leases_enabled": self.leases_enabled,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_repeat_signature": self.max_repeat_signature,
            "require_worktree": self.require_worktree,
            "task_idle_hours": self.task_idle_hours,
            "task_queue_stale_days": self.task_queue_stale_days,
            "require_task": self.require_task,
            "block_unfinished_work": self.block_unfinished_work,
            "compare_dependencies": self.compare_dependencies,
            "commit_conventions": self.commit_conventions,
            "autonomy": self.autonomy,
            "protect_trunk": self.protect_trunk,
            "manage_pull_requests": self.manage_pull_requests,
            "report_defects": self.report_defects,
            "stage_override": self.stage_override,
            "exempt_paths": self.exempt_paths,
        }


def detect_test_command(root: Path) -> list[str]:
    """Infer how this project runs its tests. Returns [] when nothing is detectable."""
    for marker, runner, command in _TEST_RUNNERS:
        if not (root / marker).exists():
            continue
        if runner == "npm":
            # A package.json without a test script, or with the npm placeholder that
            # exits 1, is not a test command.
            try:
                pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
            script = (pkg.get("scripts") or {}).get("test", "")
            if not script or "no test specified" in script:
                continue
        if runner == "pytest" and marker == "pyproject.toml" and not _has_tests(root):
            continue
        if runner == "make" and not _make_has_test(root):
            continue
        return list(command)
    if _has_tests(root):
        return ["python3", "-m", "pytest", "-q"]
    return []


def _has_tests(root: Path) -> bool:
    """Python test FILES, not merely a directory named `test`.

    A directory called `test` or `tests` says nothing about language. Jekyll, gson and
    guzzle each have one and none of them is Python, and matching on the name alone baked
    `python3 -m pytest -q` into a Ruby project's pre-push hook. pytest exits 5 for "no
    tests ran", so every push out of that repository was refused — permanently, over a
    command naming no file in it. Found by installing into eleven real repositories and
    pushing from each.
    """
    for pattern in (
        "test_*.py", "*_test.py",
        "tests/**/test_*.py", "tests/**/*_test.py",
        "test/**/test_*.py", "test/**/*_test.py",
        "*/tests/**/test_*.py", "src/**/test_*.py",
    ):
        try:
            if any(root.glob(pattern)):
                return True
        except OSError:
            continue
    return False


def _make_has_test(root: Path) -> bool:
    try:
        text = (root / "Makefile").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(line.startswith("test:") for line in text.splitlines())


# The type each key must end up as. Kept beside the dataclass rather than derived from
# its annotations, because `str | None` and `list[str]` do not survive introspection on
# 3.9 with postponed evaluation, and a schema that only works on new Pythons is worse
# than one written out.
_EXPECTED: dict[str, type] = {
    "test_command": list,
    "artifact_globs": list,
    "witness_exclude": list,
    "isolate_databases": bool,
    "worktree_setup": list,
    "clean_rerun": bool,
    "scope_drift_block": bool,
    "loop_detect": bool,
    "leases_enabled": bool,
    "lease_ttl_seconds": float,
    "max_tool_calls": int,
    "max_repeat_signature": int,
    "require_worktree": bool,
    "task_idle_hours": float,
    "task_queue_stale_days": float,
    "require_task": bool,
    "block_unfinished_work": bool,
    "compare_dependencies": bool,
    "commit_conventions": bool,
    "autonomy": str,
    "protect_trunk": bool,
    "manage_pull_requests": bool,
    "report_defects": str,
    "stage_override": str,
    "exempt_paths": list,
}

_BOOL_WORDS = {
    "true": True, "false": False, "yes": True, "no": False, "1": True, "0": False,
    # The spelling the gates print for the founder to repeat back, because "off" is the
    # word a person uses about a switch and `false` is the word a config file uses.
    "on": True, "off": False,
}

# Keys that decide whether a finish is verifiable, which is the one thing a blocked
# session has a motive to change. `claude-bp ci` owns them because it PROVES the command
# before it writes it; nothing here can, so nothing here may.
EVIDENCE_KEYS = {"test_command", "artifact_globs", "clean_rerun"}

# The founder's own words, captured by the hook that reads them and stored where no
# session can write. Tier B by decision 0001: bookkeeping about this clone, not a fact
# about the repository.
SWITCH_REQUESTS = "switch-requests.json"

# `scope_drift_block off`, `require_worktree: false`, `task_idle_hours = 4`. Deliberately
# narrow: the key is a literal this plugin printed for them to repeat, so there is no
# prose to interpret and no way for an agent to phrase its way into a match.
_SWITCH = re.compile(
    r"\b(?P<key>[a-z][a-z_]{3,31})\s*(?::|=|\s)\s*"
    r"(?P<value>on|off|true|false|yes|no|-?\d+(?:\.\d+)?)\b",
    re.I,
)


def switches_in(text: str) -> dict[str, str]:
    """Config switches the founder asked for, in their own message. Usually empty."""
    out: dict[str, str] = {}
    for found in _SWITCH.finditer(text or ""):
        key = found["key"].lower()
        if key in _EXPECTED and key not in EVIDENCE_KEYS:
            out[key] = found["value"].lower()
    return out


# The founder's acceptance of work, in the same store and on the same terms as a switch:
# a literal this plugin printed for them to repeat, recorded from THEIR message where no
# session can write it, and consumed on use.
#
# Three of them, because they authorise different things. `+merge` says a branch has been
# looked at and may land — the assistant then opens, checks and merges on its own, which
# is the whole point. `+release` says one promotion to production may happen, `+migration`
# one destructive statement. Each is spent immediately, so none can become a standing
# grant.
#
# Prose is deliberately not read. Decision 0006 rejected that for switches — "a regex
# judging language would be a gate switched by phrasing" — and acceptance is the higher
# stake of the two. Nothing the model writes reaches this either: only the founder's own
# turns pass through `prompt-capture`.
APPROVE_MERGE = "approve:merge"
APPROVE_RELEASE = "approve:release"
APPROVE_MIGRATION = "approve:migration"

# A SYMBOL, not the word "ok". The literal was `merge ok`, and the founder of this
# repository writes Russian — so the most natural thing they could say, «мерджи», opened
# nothing, and the refusal answered by asking them to say it in English instead. Adding
# Russian words was the obvious repair and is the wrong one: мерж, мердж, смержи, мержим,
# and every form missed is a refusal in the face of somebody who is certain they allowed
# it (#147).
#
# `+` carries no language. The nouns stay because they are already the words spoken in
# both — «мерж», «релиз», «миграция» are these words. Still anchored to the END of a
# line, so a sentence ABOUT a merge is not one: "we should merge okay soon" authorised
# one before that anchor existed.
_APPROVAL = re.compile(
    r"(?im)(?:^|\s)\+(?P<subject>merge|release|deploy|migration)\s*[.!]?\s*$"
)

_APPROVAL_KEYS = {
    "merge": APPROVE_MERGE,
    "release": APPROVE_RELEASE,
    "deploy": APPROVE_RELEASE,
    "migration": APPROVE_MIGRATION,
}


def approvals_in(text: str) -> dict[str, str]:
    """Acceptances the founder gave in their own message. Usually empty."""
    return {
        _APPROVAL_KEYS[found["subject"].lower()]: "yes"
        for found in _APPROVAL.finditer(text or "")
    }


def approved(ctx: GitContext, key: str) -> bool:
    """Has the founder authorised this, in a message of their own?"""
    return asked_for(ctx, key) is not None


def record_switches(ctx: GitContext, asked: dict[str, str]) -> None:
    if not asked:
        return
    path = store.tier_b(ctx, SWITCH_REQUESTS)
    record = store.read_json(path, default={})
    if not isinstance(record, dict):
        record = {}
    record.update(asked)
    store.write_json(path, record)


def asked_for(ctx: GitContext, key: str) -> str | None:
    """The value the founder asked for on this key, or None if they never did."""
    record = store.read_json(store.tier_b(ctx, SWITCH_REQUESTS), default={})
    if not isinstance(record, dict):
        return None
    found = record.get(key)
    return str(found) if found is not None else None


def clear_switch(ctx: GitContext, key: str) -> None:
    """One word authorises one change. Consumed, so it cannot be spent twice."""
    path = store.tier_b(ctx, SWITCH_REQUESTS)
    record = store.read_json(path, default={})
    if isinstance(record, dict) and record.pop(key, None) is not None:
        store.write_json(path, record)


def switch_advice(key: str, value: Any) -> str:
    """The one line every gate says instead of naming a file the session cannot write.

    A remedy the session cannot perform is worse than no remedy: the founder is told by
    the assistant that the assistant cannot do it, which reads as the assistant being
    unhelpful rather than the plugin contradicting itself (#108). And a remedy the session
    CAN perform on its own is worse still — this is the switch on a gate, and a session
    that has been blocked four times has every motive to reach for it.

    So the door exists, and the key is the founder's word.
    """
    spelled = "off" if value is False else "on" if value is True else str(value)
    return (
        f"This gate is switched by the founder, not by the session it is enforcing. If they "
        f"want it off, one line from them — `{key} {spelled}` — is the whole of it, and then: "
        f"claude-bp set {key} {spelled}"
    )


def coerce(key: str, value: Any) -> tuple[Any, str]:
    """Force a hand-edited value into the shape the code expects, or reject it.

    Returns (value, complaint); an empty complaint means it was accepted. This file is
    edited by a human in a text editor, so the realistic inputs are `"false"` for false,
    `"2000"` for a number, and a bare string where a list belongs. Every one of those
    used to be copied raw onto the dataclass. `"false"` is truthy, so a founder who
    switched leases off still had them on; `"2000"` reached an int comparison inside a
    fail-closed gate and turned one typo into "every tool call in this repository is
    blocked" for every session.
    """
    want = _EXPECTED.get(key)
    if want is None:
        return value, f"unknown key {key!r}"
    coerced = _COERCERS[want](value)
    return (coerced, "") if coerced is not None else (None, f"{key}: expected {_NAMES[want]}, got {value!r}")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _BOOL_WORDS.get(value.strip().lower())
    return None


def _as_number(value: Any) -> float | None:
    # `isinstance(True, int)` is True in Python, so booleans are excluded explicitly:
    # `"max_tool_calls": true` is a mistake, not a request for a ceiling of one.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    return None if number is None else int(number)


def _as_list(value: Any) -> list[str] | None:
    if isinstance(value, list):
        return [str(v) for v in value]
    # A bare string iterates as characters, so `"exempt_paths": "docs/"` silently became
    # five single-letter path prefixes that matched most of the tree.
    return value.split() if isinstance(value, str) else None


def _as_text(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int, float)) else None


_COERCERS = {bool: _as_bool, float: _as_number, int: _as_int, list: _as_list, str: _as_text}
_NAMES = {bool: "true or false", float: "a number", int: "a whole number", list: "a list", str: "text"}


def load(ctx: GitContext) -> Config:
    """Read the config, repairing what can be repaired and ignoring what cannot.

    A bad value falls back to the default; it never propagates into a gate. Use
    `load_checked` where the complaints should be shown to a human.
    """
    cfg, _complaints = load_checked(ctx)
    return cfg


def load_checked(ctx: GitContext) -> tuple[Config, list[str]]:
    raw = store.read_json(store.tier_a(ctx, CONFIG_NAME), default={}) or {}
    complaints: list[str] = []
    if not isinstance(raw, dict):
        raw = {}
        complaints.append(f"{CONFIG_NAME} is not a JSON object; every value defaulted")

    cfg = Config()
    known = cfg.to_dict()
    for key, value in raw.items():
        if key not in known:
            complaints.append(f"unknown key {key!r} ignored")
            continue
        if value is None:
            continue
        coerced, complaint = coerce(key, value)
        if complaint:
            complaints.append(f"{complaint} — using the default {known[key]!r}")
            continue
        setattr(cfg, key, coerced)

    # A glob's CONTENTS matter, not just its type. `Path.glob` raises on an absolute or
    # empty pattern, and that exception reached a fail-closed gate: one plausible typo in
    # a documented key blocked every tool call in the repository for every session.
    usable = [g for g in cfg.artifact_globs if g and not g.startswith(("/", "~"))]
    if len(usable) != len(cfg.artifact_globs):
        dropped = [g for g in cfg.artifact_globs if g not in usable]
        complaints.append(f"artifact_globs must be relative and non-empty; dropped {dropped}")
        cfg.artifact_globs = usable or list(DEFAULT_ARTIFACT_GLOBS)

    if cfg.autonomy not in AUTONOMY:
        complaints.append(f"autonomy {cfg.autonomy!r} is not one of {', '.join(AUTONOMY)}; using vibecode")
        cfg.autonomy = "vibecode"

    if cfg.stage_override is not None and cfg.stage_override not in ("prototype", "traction", "revenue"):
        complaints.append(f"stage_override {cfg.stage_override!r} is not a known stage; ignored")
        cfg.stage_override = None

    if not cfg.test_command:
        cfg.test_command = detect_test_command(ctx.worktree_root)
    return cfg, complaints


def save(ctx: GitContext, cfg: Config) -> Path:
    path = store.tier_a(ctx, CONFIG_NAME)
    store.write_json(path, cfg.to_dict(), mode=0o644)
    return path
