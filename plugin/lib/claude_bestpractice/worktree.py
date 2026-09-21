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
import shutil
import subprocess
from pathlib import Path

from . import store
from .gitctx import TRUNK_REFS, GitContext

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


def split_dsn(url: str) -> tuple[str, str, str]:
    """(everything before the database, the database name, the query string).

    The query is carried separately because it is not decoration: `?sslmode=require` is
    how every managed Postgres is reached, and rebuilding the URL without it produces a
    tree that cannot connect at all — a worse failure than the one isolation prevents.
    """
    head, _, tail = url.rpartition("/")
    name, mark, query = tail.partition("?")
    return head, name, mark + query


def dsn_for(ctx: GitContext, database: str) -> str:
    """This tree's DSN, keeping whatever the main checkout already points at.

    Host, port, user and password come from the founder's own `.env`; only the database
    NAME is replaced. Inventing a connection string would be this plugin guessing
    credentials it has never seen, and the guess would be wrong everywhere.
    """
    existing = database_of(main_checkout(ctx))
    if not existing or "/" not in existing:
        return f"postgresql://localhost:5432/{database}"
    head, _name, query = split_dsn(existing)
    return f"{head}/{database}{query}"


# The scheme this plugin knows how to ask about. Everything else is `worktree_setup`'s,
# exactly as before: the point is not to guess at databases in general, it is that a DSN
# saying `postgresql://` has already told us what it is.
_POSTGRES = ("postgres://", "postgresql://")

# Every Postgres server has it, and asking the SERVER is the only way to get an answer
# that means what it says: connecting to a database that is not there fails, and so does
# a server that is down, an address that is wrong, and a password that has changed.
_MAINTENANCE = "postgres"

# Long enough for a managed server across the Atlantic, short enough that an unreachable
# one does not hold up a worktree the founder is waiting for.
_PSQL_TIMEOUT = 5.0


def is_postgres(url: str) -> bool:
    return url.strip().lower().startswith(_POSTGRES)


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _identifier(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def has_client() -> bool:
    """Is there a `psql` on this machine at all?

    Asked separately from whether the server answered, because the two produce opposite
    advice and were coming out as one sentence: a developer machine talking to Postgres
    through `psycopg` from a virtualenv has no command-line client and never needed one,
    and telling that machine "could not reach the server" is both wrong and a dead end
    (#175). The server was fine. The client was never installed.
    """
    return shutil.which("psql") is not None


def _psql(dsn: str, sql: str) -> tuple[bool, str]:
    """Run one statement. (reached the server, what it printed)."""
    try:
        done = subprocess.run(
            ["psql", dsn, "-Atqc", sql], capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=_PSQL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return done.returncode == 0, done.stdout.strip()


def database_present(url: str) -> bool | None:
    """Does the database this URL names exist? None when it cannot be told.

    None is not a synonym for False anywhere it is read. No `psql`, a scheme we do not
    know, a server that is asleep — every one of those means this plugin has learned
    nothing, and saying "missing" on the strength of it would put a false alarm on the
    board of every repository that does not use Postgres.
    """
    if not is_postgres(url):
        return None
    head, name, query = split_dsn(url)
    if not name:
        return None
    reached, answer = _psql(f"{head}/{_MAINTENANCE}{query}",
                            f"select 1 from pg_database where datname = {_literal(name)}")
    return (answer == "1") if reached else None


def create_database(url: str) -> tuple[bool, str]:
    """Create the database this URL names. (done, what to say about it).

    This is the one thing in the whole isolation path that touches a server rather than a
    file, and it never happens on its own: `worktree_setup` is the founder's line, and
    this runs only when they have put it there. A plugin that issues DDL because it
    inferred that it should is a plugin that will one day infer it about production.
    """
    if not is_postgres(url):
        return False, "not a Postgres URL; `worktree_setup` is where this project says how"
    head, name, query = split_dsn(url)
    if not name:
        return False, "no database name in DATABASE_URL"
    if not has_client():
        return False, (
            f"there is no `psql` on this machine, so nothing here can create {name}. "
            "Either install one (`postgresql-client`), or let the project do it, which is "
            "what `worktree_setup` is for — a repository whose DATABASE_URL works at all "
            "already has a driver behind it"
        )
    present = database_present(url)
    if present is None:
        return False, f"could not reach the server that holds {name}"
    if present:
        return True, f"{name} is already there"
    reached, _out = _psql(f"{head}/{_MAINTENANCE}{query}",
                          f"create database {_identifier(name)}")
    return (True, f"created {name}") if reached else (False, f"the server refused to create {name}")


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


def session_on_this_database(ctx: GitContext, session_id: str = "") -> tuple[str, str] | None:
    """A live session in ANOTHER tree already on this tree's database, as (id, name).

    A tree that names no database collides with nothing: the comparison below requires a
    NON-EMPTY match, so two repositories with no database configured are not each other's
    collision. That truthiness is load-bearing, not defensive — without it every pair of
    sessions in a repository that has no database at all refuses the other, and a gate
    that fires where there is nothing to collide over is one the founder switches off for
    every project.

    ANOTHER tree, and that half was missing. Two sessions standing in one tree read one
    `.env`, so they match every time, and the only advice a same-tree collision can be
    given is "change this file" — which changes it for both of them. The refusal was
    unanswerable rather than strict.

    The registry and no `git worktree list`: this runs on every write, and the trees worth
    asking about are the ones a session is standing in, which the registry already knows.
    Trees nobody is in are `shared_database`'s question, and it is asked where the cost of
    a git call is paid once rather than per tool call.
    """
    from . import sessions

    here = database_of(ctx.worktree_root)
    if not here:
        return None
    mine = ctx.worktree_root.resolve()
    for other in sessions.live_sessions(ctx, exclude=session_id):
        try:
            tree = Path(other.worktree).resolve()
        except (OSError, TypeError, ValueError):
            continue
        if tree == mine:
            continue
        if database_of(tree) == here:
            return other.session_id, split_dsn(here)[1] or here
    return None


def shared_database(ctx: GitContext) -> tuple[Path, str] | None:
    """Another working tree of this clone naming this tree's database, as (tree, name).

    Not a liveness question, and deliberately not. A database is shared with whatever
    points at it: the main checkout, a tree made by hand, the dev server the founder runs
    out of the main checkout. None of those is a session in the registry, and asking the
    registry answered "nothing is sharing this" in the case that produced #182 — one
    worktree made with plain `git worktree add` inherited the main checkout's `.env`,
    every live session was properly isolated, and the run in that tree was still being
    perturbed by neighbours the registry could not see.

    So the question is asked of the TREES, which is where the answer is written down.
    """
    from . import gitpolicy

    here = database_of(ctx.worktree_root)
    if not here:
        return None
    mine = ctx.worktree_root.resolve()
    for tree in gitpolicy.working_trees(ctx):
        if tree != mine and database_of(tree) == here:
            return tree, split_dsn(here)[1] or here
    return None


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
        store.atomic_write(config, json.dumps(data, indent=2), mode=0o600, follow_symlink=True)
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


def record_for(ctx: GitContext, tree: Path) -> tuple[Path, dict]:
    """The record this plugin wrote for a tree, found by the path in it. ("", {}) if none."""
    try:
        records = sorted(store.tier_b(ctx, "worktrees").glob("*.json"))
    except OSError:
        return Path(""), {}
    wanted = str(tree)
    for path in records:
        body = store.read_json(path, default={}) or {}
        if isinstance(body, dict) and str(body.get("path") or "") == wanted:
            return path, body
    return Path(""), {}


DATABASE_MISSING = "database_missing"


def note_database(ctx: GitContext, tree: Path, url: str) -> bool | None:
    """Ask the server whether this tree's database is there, and write down the answer.

    Asked ONCE, when the tree is made, because that is the moment this plugin has written
    a name nothing has created — and because a probe on every session start is a database
    connection it has no business opening on a schedule.

    Returns what was learned, `None` when nothing was.
    """
    present = database_present(url)
    path, body = record_for(ctx, tree)
    # `Path("")` stringifies to `"."`, which is truthy AND is a directory — so the guard
    # read "there is a record" for every tree this plugin never made, and the write landed
    # on `os.replace(tmp, ".")`: `Device or resource busy`, out of `claude-bp database`,
    # after the database had already been created. Asked of the NAME, which is empty
    # exactly when there is no record (#216).
    if not path.name or not isinstance(body, dict):
        return present
    if present is False:
        body[DATABASE_MISSING] = True
    else:
        body.pop(DATABASE_MISSING, None)
    store.write_json(path, body)
    return present


def missing_database_line(ctx: GitContext) -> str:
    """The one alert a tree born without its database gets, and only that tree.

    Without it the tree is simply broken: every command in it fails with `database "..."
    does not exist`, which reads as a broken project rather than a setup step nobody ran,
    and the founder is the one who has to work out which switch is the cure (#167).
    """
    _path, body = record_for(ctx, ctx.worktree_root)
    if not body.get(DATABASE_MISSING):
        return ""
    name = str(body.get("database") or "this tree's database")
    return (f"DATABASE: the server has no `{name}`, which is what this tree points at. "
            f"Run `claude-bp database` to create it, and set `worktree_setup claude-bp "
            f"database` so every later tree is born with one.")


def unisolated_database_line(ctx: GitContext) -> str:
    """The one alert a worktree gets when the database it points at is not its own.

    A tree made with plain `git worktree add` never reaches the hook that derives a
    database name, and `.env` is gitignored in every project that has one — so it is born
    reading the main checkout's file and pointing at the shared database. Nothing fails
    loudly: the suite in that tree fails intermittently, in whichever file talks to the
    database, for whatever the neighbours are doing to it. Three failures, then one on a
    rerun, then 3293 passed in 23s once the tree had a database nobody else held — same
    commit every time (#182).

    A worktree only. The main checkout is where the shared database legitimately lives —
    it is the file every tree is seeded FROM — so telling it to move would break every
    tree seeded after it, which is the deadlock #181 reports from the other direction.
    """
    if not ctx.is_worktree:
        return ""
    shared = shared_database(ctx)
    if shared is None:
        return ""
    tree, name = shared
    return (f"DATABASE: this tree points at `{name}`, which is also {tree.name}'s. "
            "Worktrees isolate files and nothing else, so a suite run here can fail for "
            "the neighbours rather than for the code. `claude-bp database` after giving "
            f"DATABASE_URL in {ENV_FILE} a name nobody else holds.")


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

# The ledger, for the same reason and a louder one. Its files are written by EVERY session
# in the clone, into the one checkout they all share, so tracking them meant the founder's
# shared tree carried 67 modified cards belonging to a dozen branches — which refuses
# `git merge --ff-only`, which leaves that tree lagging `origin/main`, which makes its suite
# red on code nobody present wrote, which is #216, #217 and #218 in one chain (#219).
#
# Untracked is the founder's own answer to it: "если журнал всё же в репозитории — он обязан
# быть untracked". The cards stay exactly where v1.60.0 put them — in the main checkout, the
# one tree that outlives the others — so nothing about their durability changes, and
# `git worktree remove` still cannot take them.
_LEDGER_EXCLUDE = "/.claude/claude-bestpractice/plan/"

_EXCLUDED = (
    (_EXCLUDE_LINE, "worktrees this plugin provisions"),
    (_LEDGER_EXCLUDE, "the task ledger, written by every session and committed by none"),
)


def hide(ctx: GitContext) -> bool:
    """Keep this plugin's own files out of `git status`, once per clone.

    Without it every session start reports the plugin's own scratch trees as untracked work
    in the founder's repository — which is the exact complaint that put them outside the
    repository in the first place — and every card any session writes lands in the shared
    checkout's `git status` (#219).
    """
    exclude = ctx.common_dir / "info" / "exclude"
    try:
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    except OSError:
        return False
    present = {line.strip() for line in current.splitlines()}
    missing = [(rule, why) for rule, why in _EXCLUDED if rule not in present]
    if not missing:
        return True
    body = current if not current or current.endswith("\n") else current + "\n"
    added = "".join(f"# claude-bestpractice: {why}\n{rule}\n" for rule, why in missing)
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        store.atomic_write(exclude, body + added, mode=0o644)
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
        if not tree:
            continue
        # Whether the branch is in by CONTENT as well as by ancestry, which is the
        # difference between `git branch -d` working and leaving a branch behind on every
        # squash merge there ever was — thirty-one of the hundred and thirty-five local
        # branches on the reporting repository were merged and undeleted (#220).
        landed = _in_the_trunk(Path(tree[0]))
        if _release(ctx, tree, path, squashed=bool(landed and landed[2])):
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


def needs_a_decision(ctx: GitContext) -> list[tuple[str, str]]:
    """Trees nobody is in that somebody has to decide about: (path, branch), as git lists them.

    Two kinds, and the empty branch is the second: a tree whose branch is already in the
    trunk, which can simply go, and a tree on a DETACHED HEAD, whose commits are on no
    branch at all and disappear with the directory. Three of the thirty-eight were the
    second kind (#220).

    The sweep and the self-removal only ever touch trees THIS PLUGIN provisioned, and most
    of those thirty-eight were not ours — a session that ran its own `git worktree add`
    leaves nothing in the registry to find, and deleting another tool's directory on a guess
    is not a thing a plugin gets to do.

    So they are named instead, which is what the founder asked for as the alternative:
    "на SessionStart печатать поимённо «эти деревья принадлежат влитым веткам, их можно
    снять»". Empty in a clone whose trees are all occupied or all still being worked on,
    which is the steady state once the plugin is putting its own away.
    """
    from . import sessions
    from .gitctx import is_ancestor, trunk_ref, worktree_paths

    try:
        trunk = trunk_ref(ctx)
        trees = worktree_paths(ctx) if trunk else []
        occupied = {Path(rec.worktree).resolve() for rec in sessions.live_sessions(ctx)}
    except Exception:  # noqa: BLE001 - a naming line must never be what fails a session start
        return []
    main = main_checkout(ctx)
    out: list[tuple[str, str]] = []
    for tree in trees:
        try:
            if tree.resolve() in occupied or tree.resolve() == main.resolve():
                continue
        except OSError:
            continue
        branch = _branch_in(tree)
        if not branch or is_ancestor(ctx, branch, trunk):
            out.append((str(tree), branch))
    return out


def _branch_in(tree: Path) -> str:
    """The branch a tree stands on, or "" for a detached HEAD or a tree that is gone."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tree), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    name = proc.stdout.strip() if proc.returncode == 0 else ""
    return "" if name in ("", "HEAD") else name


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


def drop_database(url: str, database: str) -> str:
    """Give back the database this plugin made for a tree it is removing. Its name, or "".

    The tree goes and the database it pointed at stays, sixteen megabytes at a time, one
    per finished task — the second half of the pile #224 reports, and invisible where the
    first half is not: nothing lists it, `git worktree list` does not know about it, and
    the session that made it is gone.

    Two conditions here and one at every caller, all three about never dropping a database
    this plugin did not create: the URL has to be Postgres, the database it names has to be
    the name this plugin derived for that tree (`derive_db_name`, written into the record at
    provisioning) — and the callers ask `_points_at` first, so a database another working
    tree also names is never touched. That last one is what keeps `isolate_databases: false`
    safe, where every tree shares the main checkout's database and none of them owns it.

    `drop database` with no FORCE, which is the same argument the rest of this file makes:
    the server refuses while anything is connected, so a dev server still holding it keeps
    it. Nothing here overrides that.
    """
    if not database or not is_postgres(url):
        return ""
    head, name, query = split_dsn(url)
    if not name or name != database:
        return ""
    reached, _out = _psql(f"{head}/{_MAINTENANCE}{query}",
                          f"drop database {_identifier(name)}")
    return name if reached else ""


def _points_at(ctx: GitContext, url: str, exclude: Path) -> bool:
    """Does any other working tree of this clone name this database?"""
    from . import gitpolicy

    try:
        skip = exclude.resolve()
    except OSError:
        return True
    for tree in gitpolicy.working_trees(ctx):
        if tree == skip:
            continue
        if database_of(tree) == url:
            return True
    return False


def _delivers_the_same_bytes(where: Path, branch: str, trunk: str) -> bool:
    """Is every file this branch delivers already byte-identical to the trunk's?

    The proof decision 0019 accepts for `-D`, asked here of a branch NOBODY is standing on
    — `pullrequest.landed` can only answer for the tree it is called in, and the branches
    a finished tree leaves behind (`fix/…`, `docs/…` from the same session) are branches no
    tree holds at all. Same question, asked of two revisions instead of a revision and a
    working tree.

    A branch that delivers no file proves nothing and is never claimed: that is a branch
    with commits git can still see and this cannot read, which is the case for `-d`.
    """
    files = _git_lines(where, ["diff", "--name-only", f"{trunk}...{branch}"])
    if not files:
        return False
    for rel in files:
        here = _git_lines(where, ["rev-parse", "--verify", "--quiet", f"{branch}:{rel}"])
        there = _git_lines(where, ["rev-parse", "--verify", "--quiet", f"{trunk}:{rel}"])
        if not here or here != there:
            return False
    return True


def _trees_under(where: Path) -> list[Path]:
    """Every working tree of the clone `where` belongs to, asked from `where`."""
    out: list[Path] = []
    for line in _git_lines(where, ["worktree", "list", "--porcelain"]):
        if not line.startswith("worktree "):
            continue
        with contextlib.suppress(OSError):
            out.append(Path(line.split(" ", 1)[1]).resolve())
    return out


def _git_lines(where: Path, args: list[str]) -> list[str]:
    """git's stdout as lines, or [] for anything that was not a clean answer.

    Lines and not tokens: a path with a space in it is one answer, and splitting on
    whitespace turns it into two that name nothing.
    """
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(where), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()] if proc.returncode == 0 else []


def _sweepable(branch: str, held: set, trunk: str) -> bool:
    """Is this a branch the sweep may even ask git about?

    Not the trunk under any of its names, and not one a working tree is standing on — git
    would refuse that anyway, and a refusal is not how a rule should be expressed.
    """
    from . import gitpolicy

    return bool(branch) and branch not in held and branch != trunk \
        and branch not in gitpolicy.TRUNK_NAMES


def reap_merged_branches(ctx: GitContext, where: Path | None = None) -> list[str]:
    """Delete local branches whose work is in the trunk and that no working tree holds.

    A session does not make one branch. It makes the one its tree stands on and then, over
    the same afternoon, the small ones beside it — the reporting repository finished with
    `feat/card-corrections` removed and `fix/correct-sheet-one-layer` and
    `docs/one-layer-close-semantics` left behind, all three merged and deleted on GitHub
    (#224). Thirty-one of a hundred and thirty-five were in that state when #220 measured
    it, and the tree-shaped half of the sweep could never have reached them: they belong to
    no tree.

    So it is asked of the BRANCHES, once, when a tree is released. The proof is decision
    0019's and unchanged — `git branch -d`, which refuses anything that is not in by
    ancestry, and `-D` only where every file the branch delivers is already byte-identical
    to the trunk, which is what a squash merge leaves. A branch any working tree is
    standing on is never touched, and git would refuse it anyway.
    """
    # Every git call here runs in `where` and every path it needs comes from `where`.
    # `ctx` is the tree being removed on the caller that matters, and by the time this runs
    # that directory is gone — anything asked of it dies on `getcwd` inside a gate that
    # then reports nothing at all (the shape evidence-gate already carries a comment about).
    where = where or main_checkout(ctx)
    trunk = next((ref for ref in TRUNK_REFS
                  if _git_lines(where, ["rev-parse", "--verify", "--quiet", ref])), "")
    if not trunk:
        return []
    held = {_branch_in(tree) for tree in _trees_under(where)}
    candidates = [
        branch for branch in _git_lines(where, ["branch", "--format=%(refname:short)"])
        if _sweepable(branch, held, trunk)
    ]
    # `-d` first and the content test only where it refuses: the test is a `git diff` plus
    # one `rev-parse` per file, and a hundred and thirty-five branches is a number this
    # repository has actually seen.
    return [
        branch for branch in candidates
        if _delete_branch(where, branch)
        or (_delivers_the_same_bytes(where, branch, trunk)
            and _delete_branch(where, branch, forced=True))
    ]


def _release(ctx: GitContext, tree: tuple, record_path: Path, squashed: bool = False,
             notes: list[str] | None = None, force: bool = False) -> bool:
    """Hand a tree back to git, and its branch, its database and its siblings with it.

    False if git refused the removal. `notes` collects what went besides the tree, for the
    one line the founder reads — the removal used to end at the directory, and everything
    it had created around that directory stayed (#224).

    Run from the MAIN checkout, never from `ctx.worktree_root`: a session removing its own
    tree is removing the directory this process is standing in, and git obliges — it
    deletes the tree and leaves the caller with a working directory that no longer exists,
    after which every later command in that process fails on `getcwd`. Measured, not
    feared: `git worktree remove .` from inside returns 0 and the next `pwd` errors.
    """
    path, branch = tree
    where = main_checkout(ctx)
    if Path(path).resolve() == where.resolve():
        return False
    ours = _database_of_ours(ctx, Path(path))
    # `--force` is never this plugin's idea: it is passed only where the session typed it
    # itself, and every path that decides on its own to remove a tree leaves it off, so
    # git's refusal over a modified or untracked file stays the thing protecting the work.
    gone = subprocess.run(
        ["git", "worktree", "remove", *(["--force"] if force else []), path],
        cwd=str(where), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    if gone.returncode != 0:
        return False

    if branch:
        _delete_branch(where, branch, forced=squashed)
    if record_path.name:
        with contextlib.suppress(OSError):
            record_path.unlink()

    _carry_the_rest(ctx, where, branch, ours, notes if notes is not None else [])
    return True


def _carry_the_rest(ctx: GitContext, where: Path, branch: str,
                    ours: tuple | None, notes: list[str]) -> None:
    """Everything the tree was given that is not the tree: the database, and the siblings."""
    dropped = drop_database(*ours) if ours else ""
    if dropped:
        notes.append(f"the database {dropped}")
    others = [name for name in reap_merged_branches(ctx, where) if name != branch]
    if others:
        notes.append(f"{len(others)} merged branch(es): {', '.join(others[:5])}")


def _database_of_ours(ctx: GitContext, tree: Path) -> tuple[str, str] | None:
    """(url, name) when this tree's database is this plugin's to give back, else None.

    Asked BEFORE the removal, always: the tree's `.env` is the only place its DATABASE_URL
    is written down, and `git worktree remove` deletes it with everything else.
    """
    url = database_of(tree)
    _record, body = record_for(ctx, tree)
    owned = str(body.get("database") or "") if isinstance(body, dict) else ""
    if not url or not owned or _points_at(ctx, url, tree):
        return None
    return url, owned


def _delete_branch(where: Path, branch: str, forced: bool = False) -> bool:
    """Take the branch with the tree. False when git kept it, which it is entitled to do.

    `-d`, never `-D` on its own: an unmerged branch is work somebody did, and the fact that
    its session died does not make it disposable. `forced` is the one proof that overrides
    a `-d` refusal — `-d` asks whether the branch TIP is an ancestor of the trunk, and a
    squash merge makes it an ancestor of nothing, which is what this repository's own merges
    are. It is set only where every file the branch delivers is already byte-identical to
    the trunk, so what `-D` removes is a label over content that is in — exactly what
    `gh pr merge --squash --delete-branch` removes on the remote.
    """
    for flag in ("-d", "-D") if forced else ("-d",):
        done = subprocess.run(
            ["git", "branch", flag, branch], cwd=str(where), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=60,
        )
        if done.returncode == 0:
            return True
    return False


def _nothing_left_in(tree: Path) -> bool:
    """git's own answer to "would removing this lose a byte?", asked the way git asks it.

    `git status --porcelain -uall` and not `delivery.dirty`, which exempts `.claude/`: that
    exemption is right for judging whether a session delivered something and wrong here,
    where the question is whether a directory can be deleted. Ignored files are invisible
    to both this and to `git worktree remove` — verified, including the `.env` this plugin
    writes into every tree it provisions.
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(tree), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and not proc.stdout.strip()


def _board_is_clear(ctx: GitContext, session_id: str, branch: str) -> bool:
    """Has this session closed every card it holds, and closed at least one?

    At least one, because that is the difference between finished and not yet started: a
    session whose board is empty has an empty branch too, and removing the tree it is
    about to work in would be this plugin taking away what it had just provisioned.

    A paused card is read by BRANCH rather than by owner. Pausing hands the work back and
    clears the owner by design, so "nothing is mine" is true of a card whose blocker this
    session wrote ten seconds ago — and the tree it was written in is the tree that work
    is in.
    """
    from . import plan

    if plan.held_by(ctx, session_id):
        return False
    if branch and any(task.branch == branch for task in plan.load_all(ctx, plan.PAUSED)):
        return False
    return any(task.owner == session_id for task in plan.load_all(ctx, plan.DONE))


def _in_the_trunk(tree: Path) -> tuple[str, str, bool] | None:
    """(path, branch, squashed) when this tree's branch is already in the trunk, else None.

    Asked of `pullrequest.landed`, which is where this repository's one definition of
    "the work is in" lives — ancestry for an ordinary merge, content identity for a squash.
    `squashed` is the second case, and it is carried out because it decides whether
    `git branch -d` can be taken at its word.
    """
    from . import gitpolicy, pullrequest
    from .gitctx import GitError, is_ancestor, resolve

    try:
        here = resolve(tree)
    except (GitError, OSError):
        return None
    branch = here.branch
    if not branch or branch in gitpolicy.TRUNK_NAMES:
        return None
    base = gitpolicy.default_branch(here) or "main"
    if any(is_ancestor(here, branch, trunk) for trunk in (f"origin/{base}", "origin/HEAD")):
        return str(tree), branch, False
    if pullrequest.landed(here, {"branch": branch, "base": base}):
        return str(tree), branch, True
    return None


def finished(ctx: GitContext, session_id: str) -> tuple[str, str, bool] | None:
    """This session's own tree, when there is nothing left in it and nothing left to do.

    The founder's standing instruction, in their words: "когда из ворктри уже все
    замерджили и модель даже ВСЕ свои задачи закрыла — то она сама его удаляла, так ничего
    мы не теряем". Fifteen of thirty-eight trees on the reporting repository stood over a
    merged pull request, and the rule that said to remove them was written in that
    project's own instructions — an instruction is not a mechanism (#220).

    Four conditions, every one of them a fact rather than a judgement:
      - the plugin provisioned this tree for THIS session, and it is not the main checkout,
      - this session has closed every card it holds and closed at least one,
      - `git status` in the tree is empty, untracked files included,
      - the branch's work has reached the trunk, by ancestry or by content.

    The act that follows still goes through commands that REFUSE — `git worktree remove`
    without `--force` — so losing a race against a write that lands between the check and
    the removal costs the tree nothing.
    """
    tree = mine(ctx, session_id)
    if tree is None:
        return None
    try:
        if tree.resolve() == main_checkout(ctx).resolve():
            return None
    except OSError:
        return None
    found = _in_the_trunk(tree)
    if not found or not _board_is_clear(ctx, session_id, found[1]):
        return None
    return found if _nothing_left_in(tree) else None


def release_mine(ctx: GitContext, session_id: str) -> tuple[str, str, list[str]] | None:
    """Remove this session's finished tree and everything it was given along with it.

    (path, branch, what else went) when the tree is gone, None when there was nothing to do
    or git said no.
    Called from the Stop gate on the turn that finishes the work, rather than left to the
    sweep that runs when the session is already dead: the founder's complaint is not that
    the trees are never collected, it is that they pile up while they are being worked in
    — and asking them first, every time, is the other half of what they asked to stop.
    """
    found = finished(ctx, session_id)
    if not found:
        return None
    path, branch, squashed = found
    record_path, _body = record_for(ctx, Path(path))
    notes: list[str] = []
    if _release(ctx, (path, branch), record_path, squashed=squashed, notes=notes):
        return path, branch, notes
    return None


def release_now(ctx: GitContext, session_id: str, force: bool = False) -> tuple[str, str, list[str]] | None:
    """Remove this session's own tree because the session asked to, not because it is finished.

    The act is identical to `release_mine`'s and it is run from the main checkout, which is
    the whole point: `git worktree remove` on your own tree succeeds and leaves the shell
    standing in a directory that is gone, after which Claude Code refuses every git command
    the cleanup still needs — the branch, the database, the siblings — because the session
    is isolated in a worktree that no longer exists (#224).

    So the removal the session asked for happens HERE, in a hook, where nothing is standing
    in the tree and nothing is left to run afterwards. None when there is no such tree or
    when git refused it — `git worktree remove` without `--force` refuses a tree with
    anything in it, and that refusal is still the only thing deciding whether work is lost.
    """
    tree = mine(ctx, session_id)
    if tree is None:
        return None
    landed = _in_the_trunk(tree)
    branch = landed[1] if landed else _branch_in(tree)
    record_path, _body = record_for(ctx, tree)
    notes: list[str] = []
    if _release(ctx, (str(tree), branch), record_path,
                squashed=bool(landed and landed[2]), notes=notes, force=force):
        return str(tree), branch, notes
    return None


def finish_removals(ctx: GitContext) -> list[str]:
    """Finish the cleanups a tree removed by hand left half-done. What was cleaned up.

    A session that removed its own tree with plain `git worktree remove` got the directory
    and nothing else: git still holds the registration until something prunes it, the branch
    stays, the database stays, and the session could not do any of it — every git command it
    tried was refused for being isolated in a tree that no longer existed (#224). The state
    is already on the founder's machine, so the fix has to reach backwards as well as
    forwards.

    Only records THIS plugin wrote, and only for trees that are already gone from disk.
    Everything it then does is a command that refuses: `branch -d`, and `drop database`
    without FORCE.
    """
    cleaned: list[str] = []
    where = main_checkout(ctx)
    try:
        records = sorted(store.tier_b(ctx, "worktrees").glob("*.json"))
    except OSError:
        return cleaned
    for record_path in records:
        cleaned.extend(_finish_one_removal(ctx, where, record_path))
    if cleaned:
        cleaned.extend(reap_merged_branches(ctx, where))
    return cleaned


def _finish_one_removal(ctx: GitContext, where: Path, record_path: Path) -> list[str]:
    """What one record of an already-deleted tree still owns, given back. [] for the rest."""
    body = store.read_json(record_path, default={}) or {}
    path = _a_tree_that_is_gone(body)
    if path is None:
        return []
    subprocess.run(
        ["git", "worktree", "prune"], cwd=str(where), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=60,
    )
    cleaned: list[str] = []
    dropped = _give_back_recorded_database(ctx, str(body.get("database") or ""), path)
    if dropped:
        cleaned.append(dropped)
    with contextlib.suppress(OSError):
        record_path.unlink()
    cleaned.append(str(path))
    return cleaned


def _a_tree_that_is_gone(body) -> Path | None:
    """The path this record describes, when it is ours and no longer on disk."""
    if not isinstance(body, dict) or not body.get("provisioned_by_plugin"):
        return None
    where = str(body.get("path") or "")
    return None if not where or Path(where).is_dir() else Path(where)


def _give_back_recorded_database(ctx: GitContext, database: str, tree: Path) -> str:
    """The database a vanished tree left behind, dropped. Its name, or "".

    Its `.env` went with the directory, so the name comes from the record and the rest of
    the URL from the main checkout's own — which is where every tree's came from. The tree
    is already gone, so the only trees that can still be pointing at it are the ones that
    were never ours.
    """
    if not database:
        return ""
    url = dsn_for(ctx, database)
    if _points_at(ctx, url, tree):
        return ""
    return drop_database(url, database)


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


def working_context(ctx: GitContext, session_id: str) -> GitContext:
    """The context of the tree this session actually works in, from wherever it is asked.

    A hook is handed the harness's working directory, and the harness's working directory
    is the one the chat started in — the main checkout, in every session this plugin sends
    into a worktree, because `cd` inside a Bash call moves the shell and not the harness.
    So the Stop gate ran the suite, counted the diff and read the scope in a checkout the
    session had been forbidden to write in, and which in a repository with three to eight
    sessions is shared by all of them: it reported a failing test from somebody else's
    stale tree and 335 changed files belonging to nobody present, and the only way to
    clear it was to commit or update a tree this plugin's own rule says not to touch (#213).

    Only ever redirects to a tree THIS PLUGIN provisioned for THIS session and that git
    still has registered. A tree that is gone, or one nobody recorded, leaves the context
    exactly where it was — a gate that guesses which checkout to judge is worse than one
    that judges the wrong one loudly.
    """
    from .gitctx import GitError, resolve, worktree_paths

    tree = mine(ctx, session_id)
    if tree is None:
        return ctx
    try:
        if tree.resolve() == ctx.worktree_root.resolve():
            return ctx
        if tree.resolve() not in {p.resolve() for p in worktree_paths(ctx)}:
            return ctx
        return resolve(tree)
    except (GitError, OSError):
        return ctx


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
