"""Provisioning a worktree, shared by the hook that owns creation and the gate that needs one.

Extracted because the refusal used to hand the agent a command to run, and a command the
agent runs is a question the founder gets asked — either as a permission prompt for
`git worktree add`, or as the agent stopping to ask whether it should. Reported as exactly
that: a chip in the chat asking whether to use a worktree.

Neither is a decision the founder owns. The plugin's own autonomy line says to ask them for
money, legal exposure and product direction, and this is none of those — it is the plugin's
own rule being satisfied. A hook runs without a permission prompt, so the way to stop asking
is for the plugin to do it itself.

Same semantics as the `WorktreeCreate` hook, because it is the same code: outside the
repository so it never shows up in a status or a glob, trusted at birth or project settings
and hooks silently never load, and a port and database name derived per tree so two sessions
do not race on one dev server.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import store
from .gitctx import GitContext

PORT_BASE = 41000
PORT_RANGE = 900


def derive_port(worktree_path: str) -> int:
    digest = hashlib.sha256(worktree_path.encode()).hexdigest()
    return PORT_BASE + (int(digest[:8], 16) % PORT_RANGE)


def derive_db_name(repo_name: str, branch: str) -> str:
    safe = re.sub(r"[^a-z0-9_]", "_", f"{repo_name}_{branch}".lower())
    return safe[:60] or "claude_bestpractice_dev"


# `str.isalnum()` is true for Cyrillic, so a Russian prompt produced a Cyrillic directory
# AND a Cyrillic branch. Git accepts both and then: the branch goes to the remote on the
# first push, `git worktree list` prints it octal-escaped (\320\277\320\276…), and macOS
# normalises the directory name differently from Linux, so the same repository on two
# machines disagrees about whether the tree exists. Reported from a real run.
#
# Transliterated rather than dropped, because the founder writes Russian prompts and a
# branch called `work` says nothing. Anything with no ASCII left after this falls back.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def slugify(text: str, fallback: str = "work") -> str:
    """An ASCII, git-safe, filesystem-safe slug — or the fallback when nothing survives."""
    out = []
    for char in text.lower():
        if char in _TRANSLIT:
            out.append(_TRANSLIT[char])
        elif char.isascii() and char.isalnum():
            out.append(char)
        else:
            out.append(" ")
    words = "".join(out).split()[:5]
    return "-".join(words)[:60].strip("-") or fallback


def trust(path: str) -> bool:
    """Mark a worktree trusted so project settings and hooks actually load.

    In an untrusted worktree every project `permissions.allow` entry is ignored, plugin
    hooks never run, and in headless mode prompting means auto-denial — it fails safe and
    looks exactly like a model failure.
    """
    config = Path.home() / ".claude.json"
    try:
        data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False

    projects = data.setdefault("projects", {})
    if not isinstance(projects, dict):
        return False
    entry = projects.setdefault(path, {})
    if not isinstance(entry, dict):
        return False
    entry["hasTrustDialogAccepted"] = True

    try:
        store.atomic_write(config, json.dumps(data, indent=2), mode=0o600)
    except OSError:
        return False
    return True


def record(ctx: GitContext, slug: str, absolute: str, branch: str, trusted: bool,
           session_id: str = "") -> dict:
    body = {
        "path": absolute,
        "branch": branch,
        "port": derive_port(absolute),
        "database": derive_db_name(ctx.worktree_root.name, slug),
        "trusted": trusted,
        # Who it was made for, and the fact that WE made it. Both are read by the reaper:
        # it may only remove trees this plugin provisioned, for sessions that are gone.
        "session_id": session_id,
        "provisioned_by_plugin": True,
    }
    store.write_json(store.tier_b(ctx, "worktrees", f"{slug}.json"), body)
    return body


# Conventional Commits types, keyed by what the founder actually types. Russian included
# because that is what they write, and a plugin that only understands English instructions
# would silently label every one of their branches `feat/`.
#
# Order matters: the first type whose word appears wins, so the more specific verbs are
# checked before the general ones. Anything unrecognised is `feat`, which is the honest
# default — not knowing is not a reason to guess `chore`.
_BRANCH_TYPES = (
    ("fix", ("fix", "repair", "bug", "broken", "почини", "исправ", "поправ", "фикс", "чини")),
    # `document` and not `doc `: the Russian marker `документ` is a prefix and catches
    # документацию / задокументируй, while the English side required a trailing space and
    # so could not match `document`, `documentation` or `documented` — the actual words an
    # English prompt uses. `readme` was covering the rest by accident. Every other type was
    # symmetric across the two languages; this one was not. Reported as issue #35.
    ("docs", ("docs", "doc ", "document", "readme", "changelog", "документ", "доки", "докум")),
    ("refactor", ("refactor", "rewrite", "clean up", "cleanup", "рефактор", "перепиш", "почист")),
    ("test", ("test", "coverage", "тест", "покрой", "покрыт")),
    ("perf", ("perf", "optimi", "faster", "speed up", "ускор", "оптимиз", "производительн")),
    ("chore", ("bump", "upgrade dep", "dependenc", "зависимост")),
)

DEFAULT_BRANCH_TYPE = "feat"


def branch_type(task: str) -> str:
    """The `<type>` half of `<type>/<topic>`, read off the instruction.

    Every branch was `feat/` regardless of what the session was asked to do, which is a
    convention this plugin was imposing rather than following. Reported by a founder whose
    project uses the ordinary `<type>/<topic>` shape.
    """
    lowered = task.lower()
    for name, words in _BRANCH_TYPES:
        if any(word in lowered for word in words):
            return name
    return DEFAULT_BRANCH_TYPE


def session_slug(task: str, session_id: str) -> str:
    """Task-derived, and unique per session — because sharing one is the whole failure.

    Two sessions with no recorded prompt both slugged to `work`, and two sessions given the
    same instruction both slugged the same. `provision` returns an existing directory, so
    the second session would have been sent into the first one's tree — by the gate whose
    entire purpose is to stop exactly that. Reported as a naming nit; it is the silent
    overwrite, arrived at from the other side.

    The suffix is short and derived from the session, so the same session refused twice is
    still sent to the same place.
    """
    base = slugify(task)
    if not session_id:
        return base
    return f"{base}-{hashlib.sha256(session_id.encode()).hexdigest()[:8]}"


def target_for(ctx: GitContext, slug: str) -> Path:
    """Outside the repository by construction — a worktree inside the working tree shows
    up in every status, every glob and every scan the sibling sessions run."""
    return ctx.worktree_root.parent / f"{ctx.worktree_root.name}-{slug}"


def reap_unused(ctx: GitContext, live: set) -> list[str]:
    """Remove trees this plugin made for sessions that are gone and that hold no work.

    They accumulated one per task phrasing and stayed even when the refusal was the only
    thing that ever happened in them — nine on one test repository in a single run, each
    with an empty branch and an empty directory. The plugin made them unasked, so cleaning
    them up is the plugin's job too.

    Deliberately built out of commands that REFUSE rather than checks that decide:
    `git worktree remove` without `--force` will not touch a tree with modifications, and
    `git branch -d` will not delete an unmerged branch. If either has anything to say, the
    tree stays. Nothing here passes a flag that overrides a refusal, and that is the whole
    safety argument — not the conditions below, which are only there to avoid asking.
    """
    removed: list[str] = []
    directory = store.tier_b(ctx, "worktrees")
    try:
        records = sorted(directory.glob("*.json"))
    except OSError:
        return removed

    for path in records:
        tree = _abandoned(ctx, store.read_json(path, default={}) or {}, live)
        if tree and _release(ctx, tree, path):
            removed.append(tree[0])
    return removed


def _abandoned(ctx: GitContext, body: dict, live: set) -> tuple | None:
    """(path, branch) when this record describes a tree we made and nobody is in."""
    if not body.get("provisioned_by_plugin"):
        return None
    owner = str(body.get("session_id") or "")
    if not owner or owner in live:
        return None
    tree = str(body.get("path") or "")
    if not tree or Path(tree).resolve() == ctx.worktree_root.resolve():
        return None
    return tree, str(body.get("branch") or "")


def _release(ctx: GitContext, tree: tuple, record_path: Path) -> bool:
    path, branch = tree
    gone = subprocess.run(
        ["git", "worktree", "remove", path],
        cwd=str(ctx.worktree_root), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    if gone.returncode != 0:
        return False

    if branch:
        # -d, never -D: an unmerged branch is work somebody did, and the fact that its
        # session died does not make it disposable.
        subprocess.run(
            ["git", "branch", "-d", branch],
            cwd=str(ctx.worktree_root), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=60,
        )
    with contextlib.suppress(OSError):
        record_path.unlink()
    return True


def mine(ctx: GitContext, session_id: str) -> Path | None:
    """The tree this plugin already made for this session, if it is still on disk.

    The registry is the record of what was provisioned and for whom, so this asks it
    rather than re-deriving a name that has since changed.
    """
    if not session_id:
        return None
    from . import store

    try:
        records = sorted(store.tier_b(ctx, "worktrees").glob("*.json"))
    except OSError:
        return None
    for path in records:
        body = store.read_json(path, default={}) or {}
        if not body.get("provisioned_by_plugin") or body.get("session_id") != session_id:
            continue
        candidate = Path(str(body.get("path") or ""))
        if candidate.is_dir():
            return candidate
    return None


def provision(ctx: GitContext, task: str = "", session_id: str = "") -> Path | None:
    """Create the worktree this session should be working in, or None if git refused.

    Returns the existing path when it is already there, so a session that is refused twice
    is sent to the same place rather than accumulating trees.

    None is not a failure to handle loudly: the caller falls back to naming the command,
    which is where this started. Better to say something true than to crash a fail-closed
    gate over a convenience.
    """
    # One tree per SESSION, whatever the task statement says now. The name is derived from
    # the task, and the task statement is re-captured on every substantive message — so a
    # session that was refused under one instruction and again under the next got a second
    # tree with a second branch, named after a slug of whatever the founder had just said.
    # Forty-two of them across four transcripts of one day, each removed by hand (#81).
    already = mine(ctx, session_id)
    if already is not None:
        return already

    slug = session_slug(task, session_id)
    branch = f"{branch_type(task)}/{slug}"
    target = target_for(ctx, slug)
    absolute = str(target)

    if target.is_dir():
        record(ctx, slug, absolute, branch, trust(absolute), session_id)
        return target

    proc = subprocess.run(
        ["git", "worktree", "add", "-b", branch, absolute],
        cwd=str(ctx.worktree_root), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=120,
    )
    if proc.returncode != 0:
        # A branch of that name already exists is the common one, and it is recoverable:
        # attach a worktree to the branch instead of creating it again.
        attach = subprocess.run(
            ["git", "worktree", "add", absolute, branch],
            cwd=str(ctx.worktree_root), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=120,
        )
        if attach.returncode != 0:
            return None

    record(ctx, slug, absolute, branch, trust(absolute), session_id)
    return target
