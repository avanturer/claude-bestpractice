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


ENV_FILE = ".env"
ENV_EXAMPLE = (".env.example", ".env.sample", ".env.template")
_DSN_KEY = re.compile(r"^\s*(?:export\s+)?DATABASE_URL\s*=", re.M)


def database_url_in(text: str) -> str:
    """The DATABASE_URL a dotenv file declares, or "" when it declares none."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not _DSN_KEY.match(line):
            continue
        return stripped.split("=", 1)[1].strip().strip("'\"")
    return ""


def _with_database(text: str, url: str) -> str:
    """Set DATABASE_URL, replacing the line if there is one. Everything else untouched.

    Merged rather than overwritten because the file is usually the founder's: it carries
    keys, hosts and secrets that a worktree needs as much as the main checkout does, and
    rewriting it would be this plugin destroying configuration to fix a database name.
    """
    out, replaced = [], False
    for line in text.splitlines():
        if _DSN_KEY.match(line) and not line.strip().startswith("#"):
            out.append(f"DATABASE_URL={url}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"DATABASE_URL={url}")
    return "\n".join(out).strip() + "\n"


def _seed_for(tree: Path, seed_from: Path | None) -> str:
    """The dotenv body a new tree should start from.

    Its own file first. Then the MAIN CHECKOUT's, because a fresh `git worktree add`
    checks out no gitignored file and `.env` is gitignored in every project that has one
    — so without this the tree is born with a database name and nothing else: no host, no
    keys, no credentials, and the isolation is the thing that broke the session.

    `.env.example` last, for a repository whose main checkout has no `.env` either.
    """
    for candidate in (tree / ENV_FILE, (seed_from / ENV_FILE) if seed_from else None):
        if candidate is not None and candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="surrogateescape")
    for name in ENV_EXAMPLE:
        if (tree / name).is_file():
            return (tree / name).read_text(encoding="utf-8", errors="surrogateescape")
    return ""


def isolate_database(tree: Path, url: str, seed_from: Path | None = None) -> bool:
    """Give this worktree its own DATABASE_URL. False when nothing could be written.

    Worktrees isolate files and nothing else: every one of them points at the same
    database daemon, so one session's `idle in transaction` blocks every sibling's tests
    on its locks. Measured on a real repository: seventy seconds became twenty minutes,
    and the transaction holding it had been open for nearly a day (#164).

    Only the database NAME changes; see `_seed_for` for where the rest comes from.
    """
    try:
        body = _seed_for(tree, seed_from)
        (tree / ENV_FILE).write_text(_with_database(body, url), encoding="utf-8")
    except OSError:
        return False
    return True


def dsn_for(ctx: GitContext, database: str) -> str:
    """This tree's DSN, keeping whatever the main checkout already points at.

    Host, port, user and password come from the founder's own `.env`; only the database
    NAME is replaced. Inventing a connection string would be this plugin guessing
    credentials it has never seen, and the guess would be wrong everywhere.
    """
    existing = database_of(main_checkout(ctx))
    if not existing or "/" not in existing:
        return f"postgresql://localhost:5432/{database}"
    return existing.rsplit("/", 1)[0] + "/" + database


def run_setup(tree: Path, command: list[str]) -> bool:
    """Let the project bring its own database into existence. False when it could not.

    The command is the project's, because creating a database is the one part of this a
    plugin cannot know: `createdb` is Postgres, and hardcoding it breaks the first
    repository that is not. It lives in `config.json`, which `pre-tool` refuses to the
    session — so it is the founder's line, not one an agent rewrites when it is in the way.

    Fails open. A worktree that exists without its database is recoverable by hand; a
    worktree that failed to be created is a session that cannot start.
    """
    if not command:
        return True
    try:
        done = subprocess.run(
            list(command), cwd=str(tree), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def database_of(tree: Path) -> str:
    """What this tree is configured to use, or "" when it cannot be told."""
    try:
        target = tree / ENV_FILE
        if not target.is_file():
            return ""
        return database_url_in(target.read_text(encoding="utf-8", errors="surrogateescape"))
    except OSError:
        return ""


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


# Where Claude Code makes its own worktrees, and the only location its `EnterWorktree`
# enters without asking. Every other path returns `ask` from that tool's own safety check,
# which no hook approval and no `permissions.allow` entry can clear (#91, #111).
HOME = Path(".claude") / "worktrees"


def _within(path: Path, directory: Path) -> bool:
    try:
        return directory.resolve() in path.resolve().parents
    except OSError:
        return False


def main_checkout(ctx: GitContext) -> Path:
    """The tree everything is provisioned under, from whichever tree is asking.

    Anchored so trees do not nest inside trees: a session refused inside
    `.claude/worktrees/a` would otherwise be sent to `.claude/worktrees/a/.claude/
    worktrees/b`, and removing `a` would then take somebody else's tree with it.
    """
    from . import gitpolicy

    trees = gitpolicy.working_trees(ctx)
    return trees[0] if trees else ctx.worktree_root


def home_of(ctx: GitContext) -> Path:
    return main_checkout(ctx) / HOME


def target_for(ctx: GitContext, slug: str) -> Path:
    """Under `.claude/worktrees/`, because that is the one place entering never prompts.

    These were siblings of the repository, and the argument for it was real: a worktree
    inside the working tree shows up in every status, every glob and every scan the
    sibling sessions run. It was answered by the layer above — since CLI v2.1.206
    `EnterWorktree` prompts for approval on any path outside `.claude/worktrees/`,
    unconditionally, before permissions are consulted at all. So the gate ordered a move
    the founder was then asked to authorise, every time, in a repository with eight
    sibling trees (#111).

    The original argument is paid for rather than dismissed: `hide` excludes this from
    git, and `.claude/` is a dot-directory, which the search tools skip by default.
    """
    return home_of(ctx) / slug


# `.git/info/exclude` and not the founder's `.gitignore`: this is a fact about one clone,
# not about the project, and a plugin that edits a tracked file to make room for its own
# scratch space is one that shows up in the founder's next diff. Same reasoning as
# decision 0001 puts Tier B in the common dir.
_EXCLUDE_LINE = "/.claude/worktrees/"


def hide(ctx: GitContext) -> bool:
    """Keep provisioned trees out of `git status`, once per clone.

    Without this every session start reports the plugin's own scratch trees as untracked
    work in the founder's repository — which is the exact complaint that put them outside
    the repository in the first place.
    """
    exclude = ctx.common_dir / "info" / "exclude"
    try:
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    except OSError:
        return False
    if any(line.strip() == _EXCLUDE_LINE for line in current.splitlines()):
        return True
    body = current if not current or current.endswith("\n") else current + "\n"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        store.atomic_write(
            exclude,
            f"{body}# claude-bestpractice: worktrees this plugin provisions\n{_EXCLUDE_LINE}\n",
            mode=0o644,
        )
    except OSError:
        return False
    return True


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


def stranded(ctx: GitContext) -> list[str]:
    """Trees this plugin made, that nobody is standing in, that git refused to remove.

    The reaper is built out of commands that REFUSE — `git worktree remove` without
    `--force` will not touch a tree with modifications — so a session that died mid-edit
    leaves a tree no sweep will ever clear. That is correct, and it is also how eight of
    them accumulate: the plugin will not delete the work and nothing names it either.

    Reported, never removed. Naming them is the whole fix: an agent can finish one, commit
    it, or remove it deliberately; a sweep cannot decide which.
    """
    from . import sessions

    try:
        live = {record.session_id for record in sessions.live_sessions(ctx)}
    except Exception:  # noqa: BLE001 - a report must never be what breaks a session start
        return []
    out: list[str] = []
    try:
        records = sorted(store.tier_b(ctx, "worktrees").glob("*.json"))
    except OSError:
        return out
    for record in records:
        found = _abandoned(ctx, store.read_json(record, default={}) or {}, live)
        if not found:
            continue
        path = Path(found[0])
        if path.is_dir() and _holds_work(path):
            out.append(found[0])
    return out


def _holds_work(tree: Path) -> bool:
    """Is there anything in this tree a `git worktree remove` would refuse to lose?"""
    from . import delivery
    from .gitctx import GitError, resolve

    try:
        return delivery.dirty(resolve(tree))
    except (GitError, OSError):
        return False


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
    # Before the tree exists, so it is never visible as untracked work even for the moment
    # between `git worktree add` and the next status.
    hide(ctx)

    if not target.is_dir() and not add_tree(ctx, absolute, branch):
        return None

    record(ctx, slug, absolute, branch, trust(absolute), session_id)
    return target


def add_tree(ctx: GitContext, absolute: str, branch: str) -> bool:
    """`git worktree add`, with the one recoverable failure handled. False if git refused.

    Shared with the `WorktreeCreate` hook rather than written twice: that hook echoed a
    path it had never created — only the path's PARENT was made — so the harness refused
    the agent with "the hook must create the directory before echoing its path", and
    `isolation: "worktree"` could not start at all (#148).
    """
    proc = subprocess.run(
        ["git", "worktree", "add", "-b", branch, absolute],
        cwd=str(ctx.worktree_root), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=120,
    )
    if proc.returncode == 0:
        return True
    # A branch of that name already exists is the common one, and it is recoverable:
    # attach a worktree to the branch instead of creating it again.
    attach = subprocess.run(
        ["git", "worktree", "add", absolute, branch],
        cwd=str(ctx.worktree_root), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=120,
    )
    return attach.returncode == 0


# The founder settings file the permission layer reads. Project settings cannot grant this:
# `EnterWorktree` is judged by the tool's own `checkPermissions`, and only a rule in user
# settings resolves ahead of it.
USER_SETTINGS = ".claude/settings.json"
ENTER = "EnterWorktree"


def entry_permitted(home: Path | None = None) -> bool:
    """Is `EnterWorktree` already allowed in the founder's own settings?

    Read so the advice can STOP. A line that keeps appearing after it has been acted on is
    one the founder learns to scroll past, which costs the lines that matter.
    """
    import json

    path = (home or Path.home()) / USER_SETTINGS
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    allow = ((raw.get("permissions") or {}).get("allow") or []) if isinstance(raw, dict) else []
    return any(str(rule).split("(")[0].strip() == ENTER for rule in allow)


def permission_advice(ctx: GitContext | None = None, session_id: str = "") -> str:
    """The one line that removes the prompt, or nothing when there is no prompt left.

    A `PreToolUse` hook CANNOT close this, which is worth stating because it is the obvious
    idea and it is wrong: `EnterWorktree.checkPermissions` returns `ask` for any path that
    is not a Claude-managed worktree, with `decisionReason.type == "safetyCheck"` — and a
    safety-check ask overrides a hook's allow by design. The plugin approves the call and
    the founder is asked anyway, one layer below the approval (#91).

    Which is why the trees moved rather than the advice improving. Since they are made
    under `.claude/worktrees/` there is nothing to authorise, and this speaks only for a
    tree an older version left beside the repository — where the prompt is still real and
    the founder is entitled to know it is not this plugin asking (#111).
    """
    if entry_permitted():
        return ""
    if ctx is not None:
        standing = mine(ctx, session_id)
        if standing is None or _within(standing, home_of(ctx)):
            return ""
    return (
        "\nthe founder is asked to authorise every worktree entry, which this gate then "
        "requires of every session. One line in ~/.claude/settings.json ends it: add "
        '"EnterWorktree" (and "ExitWorktree") to permissions.allow. A hook cannot do this — '
        "the prompt is the tool's own safety check, which overrides any hook approval."
    )
