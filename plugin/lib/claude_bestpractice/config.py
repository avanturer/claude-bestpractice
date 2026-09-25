"""Configuration: detected, not asked for.

Every value here has a working default derived from the repository. A founder who
never writes a config file gets correct behaviour; the file exists only to override a
detection that got it wrong.

Config lives in Tier A (committed) so all worktrees and all sessions agree. Eight
sessions reading different settings is the contradictory-instruction failure this
plugin exists to prevent — which is why every tree reads the MAIN checkout's copy
(`founders_tree`) rather than its own.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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
    # Whether this plugin enforces anything at all here. Off and every gate stands down —
    # the blocking ones silently, on the same turn, without waiting for a restart.
    #
    # It exists because "off" has to mean off. A founder who had asked for the plugin to be
    # switched off for a project watched the worktree gate keep refusing writes and keep
    # provisioning trees for the rest of the session, with `claude-bp set require_worktree
    # off` refusing them from inside the session that was being blocked (#215). A gate with
    # no reachable door is one that gets uninstalled, and an uninstalled gate enforces
    # nothing at all — which is strictly worse than one the founder can silence in a line.
    #
    # Their word, never the session's: this file is on `PROTECTED_STATE`, so the party being
    # gated cannot write it, and `claude-bp set` refuses until they have said the line.
    enabled: bool = True
    test_command: list[str] = field(default_factory=list)
    # One suite per PATH, for the repositories that have more than one. A mobile app and a
    # backend in one tree are two suites, and the backend's says nothing about the app: a
    # branch touching nothing under `backend/` was refused four times over a backend test
    # that failed for the state of a local database, while the jest run that did cover the
    # change was invisible because the gate knew exactly one command (#206).
    #
    #   "test_commands": {"mobile/": "npx jest --ci", "backend/": "make test"}
    #
    # `test_command` above stays what answers for everything no entry here claims.
    test_commands: dict[str, Any] = field(default_factory=dict)
    # Whether a subproject carrying a runner AND test declarations of its own counts as a
    # suite without being named above. On, because this file exists to correct a detection
    # rather than to have one — but it is an inference about somebody's repository layout,
    # and an inference the founder cannot switch off is one they have to live with.
    #
    # Off is the STRICTER setting: the repository-wide command then answers for everything,
    # which is what happened before suites existed. That direction is why this is not an
    # evidence key — a session has nothing to gain by asking for it.
    detect_suites: bool = True
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
    # How many subagents one turn may start. Three, because that is the point past which
    # a fan-out stops being delegation and starts being the same question asked in
    # parallel: each agent pays for its own context of the repository before it reads a
    # line of the answer, and the founder pays for all of them. Zero switches it off.
    subagent_fanout: int = 3
    require_worktree: bool = True
    # A tree whose work is in the trunk and whose cards are all closed removes itself, on
    # the turn that finishes it. On, because the founder asked for it in those terms — "так
    # ничего мы не теряем и меня не будет тыркать она с разрешением" — and because the rule
    # that said to do it by hand was already written and still left fifteen of thirty-eight
    # trees standing over a merged pull request (#220). Off leaves the tree and the naming
    # line that reports it.
    remove_finished_trees: bool = True
    # Hours a branch may carry commits with no pull request before the board names it. Half
    # a day: long enough that work in progress is not nagged about, short enough that it is
    # still the same day somebody could say what the branch was for. A branch found at 126
    # commits behind the trunk, fixing something that had since been fixed twice, is what
    # this exists to prevent (#220). Zero switches the line off.
    branch_without_pr_hours: float = 12.0
    # Days an open pull request may go without movement before the board names it. Seven,
    # because three sat for a month on the reporting repository and nothing anywhere said
    # so. Zero switches it off.
    pull_request_idle_days: float = 7.0
    # Hours a claimed task may sit untouched before it goes back to the queue. The board's
    # whole claim is that it says what is in flight; a row nobody is working on is that
    # claim being false. Zero switches the sweep off.
    task_idle_hours: float = 24.0
    task_queue_stale_days: float = 21.0
    # Work that changed files while the ledger says nothing is in flight. Same shape as
    # every other Stop demand: satisfied once per session, then never seen again.
    require_task: bool = True
    # Tool calls a session may make before the Stop gate asks it, once, for what only
    # this window knows — the dead end it ruled out, the thing it learned that the diff
    # does not say. Counted on our own record rather than estimated from the transcript,
    # whose format is documented as internal. Forty is a judgement and says so: below it
    # a session has nothing to hand forward and being asked anyway teaches it that this
    # plugin is noise; far above it the ask arrives after the compaction it exists to
    # beat. The cost of the wrong number is one turn, once, and never a failed record —
    # the ask is feedback, not a refusal. Zero switches it off.
    notes_after_calls: int = 40
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
    #
    # From scope drift ONLY. Read as "cannot break anything" too, this list let a turn
    # whose whole diff was a failing test finish without the suite being run at all;
    # `evidence.material_changes` now never lets a test directory hide a change.
    exempt_paths: list[str] = field(
        default_factory=lambda: [
            ".claude/", "docs/", "README.md", "CHANGELOG.md",
            "tests/", "test/", "spec/", "__tests__/",
        ]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "test_command": self.test_command,
            "test_commands": self.test_commands,
            "detect_suites": self.detect_suites,
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
            "subagent_fanout": self.subagent_fanout,
            "require_worktree": self.require_worktree,
            "remove_finished_trees": self.remove_finished_trees,
            "branch_without_pr_hours": self.branch_without_pr_hours,
            "pull_request_idle_days": self.pull_request_idle_days,
            "task_idle_hours": self.task_idle_hours,
            "task_queue_stale_days": self.task_queue_stale_days,
            "require_task": self.require_task,
            "notes_after_calls": self.notes_after_calls,
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
        if runner == "npm" and not _npm_has_test(root):
            continue
        if runner == "pytest" and marker == "pyproject.toml" and not _has_tests(root):
            continue
        if runner == "make" and not _make_has_test(root):
            continue
        return list(command)
    if _has_tests(root):
        return ["python3", "-m", "pytest", "-q"]
    return []


def _npm_has_test(root: Path) -> bool:
    """A package.json with a test script in it — not absent, and not npm's placeholder.

    A package.json without a test script, or with the placeholder that exits 1, is not a
    test command. Its SHAPE is checked rather than assumed, because the file is the
    project's and not ours: `"scripts": ["test"]` and `"test": 1` each raised inside the
    config reader every gate calls first, so one malformed manifest refused every tool
    call in the repository and crashed the command that switches this plugin off.
    """
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
    script = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(script, str) and bool(script) and "no test specified" not in script


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
    "enabled": bool,
    "test_command": list,
    "test_commands": dict,
    "detect_suites": bool,
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
    "remove_finished_trees": bool,
    "branch_without_pr_hours": float,
    "pull_request_idle_days": float,
    "task_idle_hours": float,
    "task_queue_stale_days": float,
    "require_task": bool,
    "notes_after_calls": int,
    "block_unfinished_work": bool,
    "compare_dependencies": bool,
    "commit_conventions": bool,
    "autonomy": str,
    "protect_trunk": bool,
    "subagent_fanout": int,
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
# session has a motive to change. No command writes them: `test_command` is detected from
# the project, and where detection is wrong the founder edits `config.json`, which is on
# `PROTECTED_STATE` and therefore theirs alone.
#
# This comment used to name `claude-bp ci` as their owner. That command cannot set them —
# its verbs are fixed and none of them writes config — and the claim had been copied into
# two refusals, so a session told to make finishing verifiable was handed a command that
# errors, by three different messages. Say what is true or say nothing.
EVIDENCE_KEYS = {"test_command", "test_commands", "artifact_globs", "clean_rerun"}

# The founder's own words, captured by the hook that reads them and stored where no
# session can write. Tier B by decision 0001: bookkeeping about this clone, not a fact
# about the repository.
SWITCH_REQUESTS = "switch-requests.json"

# A switch with no key in `config.json`. Whether this clone's pushes are gated is a fact
# about the clone — the hook is per-clone and never committed, and so is the decision to be
# rid of it (`ci.declined`) — so its door is `claude-bp-ci off` rather than `claude-bp set`,
# and its key is the founder's word, read here like every other one (decision 0006).
PUSH_GATE = "pre_push"

# `scope_drift_block off`, `require_worktree: false`, `task_idle_hours = 4`. Deliberately
# narrow: the key is a literal this plugin printed for them to repeat, so there is no
# prose to interpret and no way for an agent to phrase its way into a match.
_SWITCH = re.compile(
    r"\b(?P<key>[a-z][a-z_]{3,31})\s*(?::|=|\s)\s*"
    r"(?P<value>on|off|true|false|yes|no|-?\d+(?:\.\d+)?)\b",
    re.I,
)


# The half for keys whose value is neither a word nor a number: a list of paths, or one
# name out of an enumerated set. Six settable keys are of those types, and for every one
# of them the refusal printed a line this reader could not read back. The founder wrote
# `worktree_setup ['bash', 'infra/scripts/worktree_db_init.sh']` verbatim, restarted the
# window, wrote it again — and the gate asked a third time, with the same line (#166).
#
# Anchored to a whole line, because there is no closed set of values to recognise here:
# the value is whatever the founder was asked to repeat, so it is the LINE that has to be
# theirs. That looseness costs nothing, because recording a request is not the switch —
# `claude-bp set` still refuses unless the value it is being asked to write is the value
# on that line. A stray sentence therefore authorises a change nobody would attempt.
#
# The key has to START the line, after nothing but the marks a chat wraps a line in. Our
# own refusal quotes the line mid-sentence ("Ask them, and one line back is enough: …"),
# and a founder pasting that refusal back is showing it, not saying it.
_SWITCH_LINE = re.compile(
    r"(?im)^[\s`>*\-]*(?P<key>[a-z][a-z_]{3,31})[ \t]*(?::|=|[ \t])[ \t]*(?P<value>\S[^\n]*)$"
)

# What a value is wrapped in when it travels through a chat and a shell: backticks from
# our own message, quotes from the `claude-bp set` line the founder copied.
_WRAPPERS = "`\"' \t"

_SPOKEN_TYPES = (list, str)


def _spoken_value(key: str, value: str) -> str:
    """The founder's typing, reduced to what `coerce` will be handed."""
    text = value.strip().rstrip(".!").strip(_WRAPPERS)
    if _EXPECTED.get(key) is not list:
        return text
    if text.startswith("[") and text.endswith("]"):
        # The spelling that shipped before this reader existed, and the one still sitting
        # in the founder's scrollback. Read too, so an upgrade does not answer a line
        # they have already written twice by asking for a third variant of it.
        text = text[1:-1].replace(",", " ")
    return " ".join(word.strip(_WRAPPERS) for word in text.split())


# Sentences this plugin prints AROUND a switch. The founder pastes our refusal back —
# it is the most quotable thing on their screen, and this whole mechanism asks them to
# copy a line out of it — and every one of those sentences CARRIES the literal it is
# asking for. Read naively, `the founder has not asked for scope_drift_block off` grants
# scope_drift_block off: the message saying the word is missing becomes the word, and a
# gate that prints its own key is not switched by the founder at all (decision 0006).
#
# Struck line by line, not message by message, so a founder who pastes the refusal and
# adds a line of their own still has their line read.
_OUR_VOICE = (
    "has not asked for",
    "one line back is enough",
    "gate is switched by the founder",
    "session that has just been blocked",
    "claude-bp set ",
)


def _their_own_words(text: str) -> str:
    """The message with this plugin's own sentences taken back out of it."""
    return "\n".join(
        "" if any(ours in line.lower() for ours in _OUR_VOICE) else line
        for line in (text or "").splitlines()
    )


def switches_in(text: str) -> dict[str, str]:
    """Config switches the founder asked for, in their own message. Usually empty."""
    out: dict[str, str] = {}
    text = _their_own_words(text)
    for found in _SWITCH_LINE.finditer(text or ""):
        key = found["key"].lower()
        if _EXPECTED.get(key) in _SPOKEN_TYPES and key not in EVIDENCE_KEYS:
            out[key] = _spoken_value(key, found["value"])
    for found in _SWITCH.finditer(text or ""):
        key = found["key"].lower()
        if _a_switch(key):
            out[key] = found["value"].lower()
    return out


def _a_switch(key: str) -> bool:
    """A key the founder throws by saying it: a settable config key, or the push gate."""
    return key == PUSH_GATE or (key in _EXPECTED and key not in EVIDENCE_KEYS)


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
# The pull requests one `+merge` accepted when the session it was said to had some open:
# each of them, once, rather than whichever merge came next (`pullrequest.accept_merges`).
MERGE_POOL = "approve:merge:pool"

# A SYMBOL, not the word "ok". The literal was `merge ok`, and the founder of this
# repository writes Russian — so the most natural thing they could say, «мерджи», opened
# nothing, and the refusal answered by asking them to say it in English instead. Adding
# Russian words was the obvious repair and is the wrong one: мерж, мердж, смержи, мержим,
# and every form missed is a refusal in the face of somebody who is certain they allowed
# it (#147).
#
# `+` carries no language. The nouns stay because they are already the words spoken in
# both — «мерж», «релиз», «миграция» are these words.
#
# THE LINE IS THE TOKEN AND NOTHING ELSE. Anchoring to the end of a line was the previous
# rule and it read a refusal as consent: «пока не вливай, я не говорил +merge» ends with
# the token, so the sentence withholding the merge authorised it. The founder said almost
# exactly that, two unapproved changes reached the trunk, and one of them nearly shipped
# to people alongside a neighbouring session's OTA publish (#192).
#
# No amount of reading the words around it fixes that — a grant cannot be inferred from
# prose it happens to end. So the shape carries the whole meaning: a line whose entire
# content is `+merge` is a deliberate act and cannot be a sentence about one. It costs
# the founder a newline before the word; the other direction cost a revert.
_APPROVAL = re.compile(
    r"(?im)^\s*\+(?P<subject>merge|release|deploy|migration)\s*[.!]?\s*$"
)

_APPROVAL_KEYS = {
    "merge": APPROVE_MERGE,
    "release": APPROVE_RELEASE,
    "deploy": APPROVE_RELEASE,
    "migration": APPROVE_MIGRATION,
}


def approvals_in(text: str) -> dict[str, str]:
    """Acceptances the founder gave IN THEIR OWN MESSAGE. Usually empty.

    The caller is responsible for handing this the founder's words and nothing else. A
    grant is the one thing in this plugin that the gated party must never be able to
    write, and the plugin's own voice reaches the prompt reader as an ordinary user turn
    — that road produced #106, #118, #166 and v1.52.0, each time on the task statement.
    Here it lands on the grant, which is decision 0008 inverted by the plugin itself.
    """
    return {
        _APPROVAL_KEYS[found["subject"].lower()]: "yes"
        for found in _APPROVAL.finditer(text or "")
    }


def approved(ctx: GitContext, key: str) -> bool:
    """Has the founder authorised this, in a message of their own?"""
    return asked_for(ctx, key) is not None


# How long a gate lets the founder's word land before refusing without it. Their message can
# reach the session before `prompt-capture` has recorded it: twice in a row the first merge
# after `+merge` was refused as unaccepted and the same merge seconds later went through,
# with nothing said in between, and each time the session asked the founder for the word
# again (#232). Only a call that would otherwise be refused pays it, and it stays well inside
# the fifteen seconds the harness gives `pre-tool`, past which the call would go through
# unjudged.
ACCEPTANCE_GRACE = 5.0

# Shortens that wait, and cannot lengthen it. A refusal the suite provokes paid the full
# five seconds for a word nobody was going to send: about four of the fourteen minutes of
# `make check`, and five seconds of every `claude-bp doctor`. Shorter only ever refuses
# sooner, so a session that set it would gain nothing; longer would run `pre-tool` past the
# harness's timeout, where the call goes through unjudged, so it is not accepted.
GRACE_ENV = "CLAUDE_BESTPRACTICE_ACCEPTANCE_GRACE"


def acceptance_grace() -> float:
    """`ACCEPTANCE_GRACE`, or less where `GRACE_ENV` asks for less. Never more."""
    try:
        asked = float(os.environ.get(GRACE_ENV, ACCEPTANCE_GRACE))
    except ValueError:
        return ACCEPTANCE_GRACE
    return min(ACCEPTANCE_GRACE, max(0.0, asked))


def awaited(ctx: GitContext, key: str) -> bool:
    """`approved`, allowing the founder's word `acceptance_grace()` seconds to be recorded."""
    return bool(within_grace(lambda: approved(ctx, key)))


def within_grace(check: Callable[[], Any]) -> Any:
    """`check()`, asked again for up to `acceptance_grace()` seconds until it answers."""
    until = time.monotonic() + acceptance_grace()
    while True:
        found = check()
        if found or time.monotonic() >= until:
            return found
        time.sleep(0.2)


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


def spell(value: Any) -> str:
    """A value written the way the founder is asked to say it back.

    A list used to be spelled with `str()`, so the line the refusal printed was a Python
    repr — `['bash', 'infra/scripts/worktree_db_init.sh']` — which nothing here could read
    back and no shell would carry unquoted (#166).
    """
    if value is True:
        return "on"
    if value is False:
        return "off"
    if isinstance(value, (list, tuple)):
        # An empty list still has to be written as something. The key alone on a line is a
        # sentence about the key, not a value, and neither reader would take it as one.
        return " ".join(str(item) for item in value) or '""'
    return str(value)


def is_only_a_switch(text: str) -> bool:
    """Is this message nothing but the founder's word on a gate?

    `worktree_setup bash infra/scripts/worktree_db_init.sh` is a key and a value. It is
    not a statement of work in any project — but it is long enough and it names a path,
    so it became the session's task, the title of a board card, and the sentence every
    scope-drift refusal quoted back (#166). Both readers see the line; only one keeps it.

    Only recognised keys are struck out. Every English instruction begins with a word and
    continues with others — "update the parser first" has the shape exactly — so a rule
    that blanked any key-shaped line would swallow the statements it exists to protect.
    """
    body = text or ""
    if not body.strip():
        return False
    for pattern in (_SWITCH_LINE, _SWITCH):
        body = pattern.sub(_blank_if_ours, body)
    return not body.strip()


def _blank_if_ours(found: "re.Match[str]") -> str:
    return " " if _a_switch(found["key"].lower()) else found.group(0)


def switch_advice(key: str, value: Any) -> str:
    """The one line every gate says instead of naming a file the session cannot write.

    A remedy the session cannot perform is worse than no remedy: the founder is told by
    the assistant that the assistant cannot do it, which reads as the assistant being
    unhelpful rather than the plugin contradicting itself (#108). And a remedy the session
    CAN perform on its own is worse still — this is the switch on a gate, and a session
    that has been blocked four times has every motive to reach for it.

    So the door exists, and the key is the founder's word.
    """
    spelled = spell(value)
    return (
        f"This gate is switched by the founder, not by the session it is enforcing. If they "
        f"want it off, one line from them — `{key} {spelled}` — is the whole of it, and then: "
        f"claude-bp set {key} {shlex.quote(spelled)}"
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
        number = float(value)
    except (ValueError, OverflowError):
        return None
    # Finite, or not a number at all. `"inf"`, `"nan"` and `1e999` all parse as floats, and
    # the first whole-number key handed one raised — `int(inf)` is an OverflowError — inside
    # the reader every gate calls first, so every tool call was refused. Where nothing
    # raised it was quieter and no better: an infinite lease never expires, and NaN
    # compares false with everything, so a sweep asked "is this above zero" read it as off.
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    return None if number is None else int(number)


def _as_list(value: Any) -> list[str] | None:
    if isinstance(value, list):
        return [str(v) for v in value]
    # A bare string iterates as characters, so `"exempt_paths": "docs/"` silently became
    # five single-letter path prefixes that matched most of the tree.
    if not isinstance(value, str):
        return None
    # Wrappers come off here for the same reason they come off in the reader: this is the
    # end of a line that travelled through a chat and a shell. `""` is how an empty list
    # is written, and it has to arrive as one from both directions.
    return [word for word in (w.strip(_WRAPPERS) for w in value.split()) if word]


def _as_text(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int, float)) else None


def _as_map(value: Any) -> dict[str, Any] | None:
    """A path-to-command map, with every entry that cannot be one dropped.

    Dropped rather than rejected, because this key is a list of independent statements and
    one malformed entry is not a reason to lose the others — a typo in the third suite must
    not silently take the first two down with it, which is what returning None would do.
    """
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for where, command in value.items():
        if isinstance(where, str) and where.strip() and isinstance(command, (str, list)):
            out[where.strip()] = command
    return out


_COERCERS = {bool: _as_bool, float: _as_number, int: _as_int, list: _as_list, str: _as_text,
             dict: _as_map}
_NAMES = {bool: "true or false", float: "a number", int: "a whole number", list: "a list",
          str: "text", dict: "a map of path to test command"}


def load(ctx: GitContext) -> Config:
    """Read the config, repairing what can be repaired and ignoring what cannot.

    A bad value falls back to the default; it never propagates into a gate. Use
    `load_checked` where the complaints should be shown to a human.
    """
    cfg, _complaints = load_checked(ctx)
    return cfg


# One lookup per process and clone. Every gate asks on every tool call, and the answer is a
# `git worktree list` that cannot change while the process lives: the main checkout of a
# clone is wherever its `.git` directory is. None where the clone has no main checkout.
_MAIN_CHECKOUT: dict[str, Path | None] = {}


def founders_tree(ctx: GitContext) -> Path:
    """The checkout whose config and settings speak for every tree of this clone.

    The main one, from whichever tree is asking, as `plan.plan_dir` already resolves it.
    A worktree's copy of `config.json` is its branch's snapshot, and `settings.local.json`
    is in no branch at all, so the founder's word — an edit not yet committed, a `claude-bp
    set`, the local switch — is only ever in the main checkout. Read per tree, `enabled off`
    stood the plugin down there and nowhere a session was working, because sessions work
    in worktrees by default, and the founder's `test_command` gave way to a detected one
    in the Stop gate the same way. The harness reads the local settings file from the main
    checkout for the same reason.

    `git worktree list` names the git directory itself as the main tree of a bare clone and
    of one made with `--separate-git-dir`. Neither is a checkout, so there the tree asking
    is the tree that answers.
    """
    # The main checkout asking is its own answer: no subprocess, and the right answer even
    # where the listing would name a separate git directory instead of it.
    if not ctx.is_worktree:
        return ctx.worktree_root
    key = str(ctx.common_dir)
    if key not in _MAIN_CHECKOUT:
        from . import worktree

        try:
            main: Path | None = worktree.main_checkout(ctx)
        except Exception:  # noqa: BLE001 - an unlistable clone still has its own tree
            main = None
        # A linked worktree is never the main tree; hearing that it is means git could not
        # list the trees, and that answer is this tree's alone, not the clone's.
        if main == ctx.worktree_root:
            return ctx.worktree_root
        _MAIN_CHECKOUT[key] = main if main is not None and (main / ".git").exists() else None
    return _MAIN_CHECKOUT[key] or ctx.worktree_root


def config_path(ctx: GitContext) -> Path:
    """The `config.json` every tree reads — and the one `claude-bp set` writes."""
    return founders_tree(ctx) / store.TIER_A_DIRNAME / CONFIG_NAME


def _shown(ctx: GitContext, path: Path) -> str:
    """A path as a session standing in this tree should read it."""
    try:
        return path.relative_to(ctx.worktree_root).as_posix()
    except ValueError:
        return str(path)


def _read_config(path: Path) -> tuple[Any, str]:
    """The founder's file as it parses, and what is wrong with it when it does not.

    `read_json` answers its default for a missing file and a broken one alike, and here
    those are opposite facts: no file is a founder content with every default, and a broken
    one is a founder whose every key — `enabled` among them — is being ignored in silence.
    A trailing comma was enough, and nothing anywhere said so.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError) as exc:
        return {}, f"{CONFIG_NAME} cannot be read ({exc})"
    try:
        return json.loads(text), ""
    except ValueError as exc:
        return {}, f"{CONFIG_NAME} does not parse ({exc})"


def load_checked(ctx: GitContext) -> tuple[Config, list[str]]:
    """The config, and every complaint about it a human should be shown.

    Shown on the board and by `claude-bp status`, because a complaint only this function
    could see was one nobody saw.
    """
    raw, broken = _read_config(config_path(ctx))
    raw = raw or {}
    complaints: list[str] = [f"{broken}, so every key is at its default"] if broken else []
    if not isinstance(raw, dict):
        raw = {}
        complaints.append(f"{CONFIG_NAME} is not a JSON object; every value defaulted")

    cfg = Config()
    known = cfg.to_dict()
    for key, value in raw.items():
        if key not in known:
            # `$comment` and its kind are notes: JSON has no comments, and this plugin's
            # own hooks.json uses the same convention. Theirs to keep, not to hear about.
            if not key.startswith("$"):
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


# What the harness calls this plugin where enablement is recorded. The marketplace name
# and the plugin name, which is the spelling `enabledPlugins` uses.
PLUGIN_KEY = "claude-bestpractice@claude-bestpractice"

# Where a project records which plugins it wants. Read and never written: this is the
# founder's file and the harness's schema, and a gate that edits the settings deciding
# whether it runs is a gate that decides for itself.
PROJECT_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")


def disabled_for_project(ctx: GitContext) -> str:
    """The project settings file that switches this plugin off, or "" when none does.

    The harness reads `enabledPlugins` when a session starts and keeps running the hooks it
    already loaded, so a founder who switches the plugin off mid-session is refused by it
    for the rest of that session — worktrees provisioned, writes blocked, and the switch
    they just threw having no effect until they restart (#215). Nothing here can unload a
    hook; what it can do is stand down when the answer is written down.

    Asked of the main checkout first, where the founder throws it, and then of the tree
    asking: a copy there is one the harness also reads for a session started in it.
    """
    for root in dict.fromkeys((founders_tree(ctx), ctx.worktree_root)):
        for rel in PROJECT_SETTINGS:
            raw = store.read_json(root / rel, default={})
            wanted = raw.get("enabledPlugins") if isinstance(raw, dict) else None
            if isinstance(wanted, dict) and wanted.get(PLUGIN_KEY) is False:
                return _shown(ctx, root / rel)
    return ""


def enforcing(ctx: GitContext) -> tuple[bool, str]:
    """Whether the gates run here at all, and what switched them off when they do not.

    One answer for every gate, because "off" meaning off in three gates and not in the
    fourth is the shape this is fixing rather than a smaller version of it.
    """
    if not load(ctx).enabled:
        return False, f"`enabled off` in {_shown(ctx, config_path(ctx))}"
    off = disabled_for_project(ctx)
    if off:
        return False, f"{off} switches this plugin off for this project"
    return True, ""


def save(ctx: GitContext, cfg: Config) -> Path:
    path = config_path(ctx)
    store.write_json(path, cfg.to_dict(), mode=0o644)
    return path


def set_key(ctx: GitContext, key: str, value: Any) -> Path:
    """Write one key into the founder's file, and leave every other key as they wrote it.

    `claude-bp set` used to `save` the whole defaulted view: their `$comment` and every key
    this version does not know were deleted, every default was pinned into a committed file
    (the pattern repairs 0004 and 0006 exist to undo), and the DETECTED `test_command` was
    written in as if the founder had chosen it — an evidence key no command may set. On a
    file that did not parse it wrote the defaults over it.

    Raises ValueError, saying what is wrong, rather than write over a file it cannot read.
    Their keys keep their order and a new one goes last, so the change reads as the one
    they asked for.
    """
    path = config_path(ctx)
    raw, broken = _read_config(path)
    if broken:
        raise ValueError(broken)
    if not isinstance(raw, dict):
        raise ValueError(f"{CONFIG_NAME} is not a JSON object")
    raw[key] = value
    store.atomic_write(path, store.dumps(raw, indent=2) + "\n", mode=0o644)
    return path
