"""A scoped rule is in the conversation before the act it governs (decisions 0113, 0114).

Claude Code loads a rule with `paths:` only when a session reads, writes or edits a file the
globs match, and drops it at compaction until such a file is touched again
(code.claude.com/docs/en/memory.md). That is what lets a large instruction layer cost nothing
in a session that never goes near its subject. It leaves three holes, all measured on the
founder's product repository: a NEW file is written with no read first, so its rule arrives
after the write; a plan made from an Explore subagent's summary carries none of the rules the
subagent saw; and an act on no file at all (a release, `ssh` to production, SQL on the live
database) loads nothing, because no path was touched.

So the plugin closes them. The hooks module reports when a rule actually reaches a loop's
request (`prompt.attachment` of type `nested_memory`, per main conversation and per subagent)
and when the session reads a rule file itself; `checkpoint` forgets a loop's rules at
compaction. `pre-tool` refuses, ONCE per rule per loop, a write whose path a scoped rule's
`paths:` matches, or a command its `commands:` matches, while that rule is not in the loop.
Once: if the report never arrives, the second attempt goes through, so a missed signal costs
one refusal and never a wall.

`commands:` is this plugin's field. Claude Code reads only `paths` from a rule and ignores
the rest, so a rule carrying both loads beside its files and is also asked for before its
commands. A rule with `commands:` alone has no `paths:` and loads at every launch anyway.

Read only where the module said it is running for that session: without the module there is
no report of a load, and refusing then would refuse every scoped write.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import store
from .gitctx import GitContext

RULES_DIR = ".claude/rules"
STATE_DIR = "rules-in-context"
# Claude Code truncates a path-scoped rule past 40,000 characters (at a 200K window).
LOAD_LIMIT_CHARS = 40_000
# Auto memory loads the first 200 lines or 25 KB of MEMORY.md, whichever comes first.
MEMORY_LIMIT_BYTES = 25_000
MEMORY_LIMIT_LINES = 200
# Measured against `/context` on the founder's mixed Russian and English rules: 58,385 bytes
# were 16.3k tokens. Bytes, not characters: Cyrillic is two bytes a character and close to
# twice the tokens, so a character count understated those files by about a third.
BYTES_PER_TOKEN = 3.55
# A session's record outlives it by this much, then the next session start removes it.
KEEP_DAYS = 7
# The words a line carries when its author meant it for every session. Moved from a file
# loaded at launch into a scoped one, such a line stops applying where it was meant to.
_EVERYWHERE = re.compile(
    r"во всех чатах|в каждом чате|в каждой сессии|всегда|железно|никогда|"
    r"\bevery (?:session|chat|turn)\b|\balways\b|\bnever\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Rule:
    """One instruction file that names when it applies."""

    path: Path
    shown: str
    paths: tuple[str, ...]
    commands: tuple[str, ...]


# --- reading a rule's scope -------------------------------------------------------------------

def _block(text: str) -> str:
    """The frontmatter between the first two `---` lines, or ""."""
    if not text.startswith("---"):
        return ""
    lines = text.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index])
    return ""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def field(text: str, key: str) -> tuple[str, ...]:
    """A frontmatter list in any of the three spellings rule files use.

    `key: a, b` (this plugin's decision records), `key: ["a", "b"]` (inline YAML) and a
    `- a` list under `key:`. Quotes come off each item.
    """
    lines = _block(text).splitlines()
    for index, line in enumerate(lines):
        if re.match(rf"{re.escape(key)}\s*:", line):
            value = line.split(":", 1)[1].strip()
            found = _inline_items(value) if value else _listed_items(lines[index + 1:])
            return tuple(item for item in found if item)
    return ()


def _inline_items(value: str) -> list[str]:
    inner = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    return [_unquote(part) for part in _split_items(inner)]


def _listed_items(lines: list[str]) -> list[str]:
    """The `- item` lines under a key, up to the next key."""
    found = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            found.append(_unquote(stripped[2:]))
        elif stripped and not line.startswith((" ", "\t")):
            break
    return found


def _split_items(value: str) -> list[str]:
    """Split on commas outside quotes and braces: `"src/**/*.{ts,tsx}", docs/*` is two."""
    items, current, depth, quote = [], [], 0, ""
    for char in value:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == "{":
            depth += 1
            current.append(char)
        elif char == "}":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


# --- matching ---------------------------------------------------------------------------------

_TOKEN = re.compile(r"\*\*/|/\*\*$|\*\*|\*|\?|\[[^\]]*\]|.", re.DOTALL)


def _expand(pattern: str) -> list[str]:
    """Brace expansion, innermost first: `src/*.{ts,tsx}` is `src/*.ts` and `src/*.tsx`."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[:match.start()], pattern[match.end():]
    return [out for option in match.group(1).split(",") for out in _expand(head + option + tail)]


@functools.lru_cache(maxsize=512)
def glob_regex(pattern: str) -> re.Pattern[str]:
    """A rule's glob as Claude Code reads it, relative to the project root.

    `**` crosses directories, `*` and `?` stay inside one, `[...]` is a class, `{a,b}` is
    either. A pattern with no slash still names a path from the root (`MEMORY.md` is the file
    at the top), as in the documentation's examples.
    """
    alternatives = ["".join(_token_regex(token) for token in _TOKEN.findall(one))
                    for one in _expand(pattern.strip().lstrip("/"))]
    return re.compile("(?:" + "|".join(alternatives) + ")")


_WILDCARDS = {"**/": "(?:.*/)?", "/**": "(?:/.*)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}


def _token_regex(token: str) -> str:
    if token in _WILDCARDS:
        return _WILDCARDS[token]
    if token.startswith("[") and len(token) > 2:
        return "[" + ("^" + token[2:-1] if token[1] == "!" else token[1:-1]) + "]"
    return re.escape(token)


def matches(rule: Rule, relative: str) -> bool:
    relative = relative.replace(os.sep, "/").removeprefix("./")
    return any(glob_regex(pattern).fullmatch(relative) for pattern in rule.paths)


def commanded(rule: Rule, command: str) -> bool:
    for pattern in rule.commands:
        try:
            if re.search(pattern, command):
                return True
        except re.error:
            # A pattern that is not a valid expression is read as words.
            if pattern in command:
                return True
    return False


# --- the rules here ---------------------------------------------------------------------------

def _user_rules_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "rules"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def rules(ctx: GitContext, user_dir: Path | None = None) -> list[Rule]:
    """Every rule file a session here loads, project and user, with what scopes it.

    Rules that scope nothing are listed too: `check` reports them, the gate passes them by.
    """
    found: list[Rule] = []
    places = [(ctx.worktree_root / RULES_DIR, ctx.worktree_root, "")]
    user = user_dir if user_dir is not None else _user_rules_dir()
    places.append((user, user, "~/.claude/rules/"))
    for directory, base, prefix in places:
        if not directory.is_dir():
            continue
        for path in sorted(store.files_under(directory)):
            if not path.name.endswith(".md"):
                continue
            text = _read(path)
            shown = prefix + path.relative_to(base).as_posix()
            found.append(Rule(path, shown, field(text, "paths"), field(text, "commands")))
    return found


# --- what is in each loop ---------------------------------------------------------------------

def _state_path(ctx: GitContext, harness: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", harness)[:120] or "unknown"
    return store.tier_b(ctx, STATE_DIR, f"{safe}.json")


def heard(ctx: GitContext, harness: str) -> None:
    """The module is running for this session, so its reports can be relied on."""
    if not harness:
        return
    store.ensure_dir(store.tier_b(ctx, STATE_DIR))
    with store.guarded_json(_state_path(ctx, harness), default={}) as box:
        box[0]["module"] = time.time()
    _prune(ctx)


def _prune(ctx: GitContext) -> None:
    cutoff = time.time() - KEEP_DAYS * 86_400
    for path in store.tier_b(ctx, STATE_DIR).glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _mark(ctx: GitContext, harness: str, loop: str, shown: list[str], key: str) -> None:
    if not harness or not shown:
        return
    store.ensure_dir(store.tier_b(ctx, STATE_DIR))
    with store.guarded_json(_state_path(ctx, harness), default={}) as box:
        loops = box[0].setdefault(key, {})
        have = set(loops.get(loop, []))
        loops[loop] = sorted(have | set(shown))


def loaded(ctx: GitContext, harness: str, loop: str, *, text: str = "", path: str = "") -> list[str]:
    """Record the rules a loop now has: those an attachment's text names, or the rule file read.

    By absolute path only, which is how the engine frames a file it attaches. A rule's text
    often names another rule by its path from the root (`see .claude/rules/x.md`), and taking
    that as a load would mark a rule present that the loop has never seen.
    """
    found = []
    for rule in rules(ctx):
        whole = str(rule.path)
        if text and whole in text:
            found.append(rule.shown)
        elif path and os.path.realpath(path) == os.path.realpath(whole):
            found.append(rule.shown)
    _mark(ctx, harness, loop, found, "loaded")
    return found


def compacted(ctx: GitContext, harness: str, loop: str) -> None:
    """Compaction summarised the loop's scoped rules away; they reload on the next touch."""
    path = _state_path(ctx, harness)
    if not harness or not path.is_file():
        return
    with store.guarded_json(path, default={}) as box:
        for key in ("loaded", "asked"):
            box[0].get(key, {}).pop(loop, None)


def owed(ctx: GitContext, harness: str, loop: str, *, relative: str = "",
         command: str = "") -> list[Rule]:
    """The scoped rules this act needs that the loop does not have and was not yet asked for.

    Each is recorded as asked, so the same rule is never refused twice in one loop between
    compactions: a load the module failed to report costs one refusal, not a wall.
    """
    state = _listening(ctx, harness)
    if state is None:
        return []
    have = set(state.get("loaded", {}).get(loop, [])) | set(state.get("asked", {}).get(loop, []))
    needed = [rule for rule in rules(ctx)
              if rule.shown not in have and _governs(rule, relative, command)]
    _mark(ctx, harness, loop, [rule.shown for rule in needed], "asked")
    return needed


def _listening(ctx: GitContext, harness: str) -> dict | None:
    """This session's record, where the module said it runs; None where it did not."""
    path = _state_path(ctx, harness)
    if not harness or not path.is_file():
        return None
    state = store.read_json(path, default={})
    return state if isinstance(state, dict) and state.get("module") else None


def _governs(rule: Rule, relative: str, command: str) -> bool:
    by_path = bool(relative and rule.paths and matches(rule, relative))
    return by_path or bool(command and rule.commands and commanded(rule, command))


def refusal(needed: list[Rule], subject: str) -> str:
    named = ", ".join(f"`{rule.shown}`" for rule in needed)
    reading = " and ".join(f"`{rule.path}`" for rule in needed)
    return (
        f"claude-bestpractice: {named} governs {subject}, and is not in this conversation: a new "
        "file is written before its rule loads, a plan from a subagent's summary carries none, "
        f"and compaction drops one until its files are touched again. Read {reading}, then do "
        "this again. Asked once per rule: the second attempt goes through."
    )


# --- what the layer costs ---------------------------------------------------------------------

def tokens(size_bytes: int) -> int:
    return round(size_bytes / BYTES_PER_TOKEN)


def memory_index(ctx: GitContext) -> Path | None:
    """This repository's auto-memory index, where Claude Code keeps it on this machine.

    The project key is the main checkout's path with every character that is not a letter or
    a digit turned into `-`; an older spelling replaced only the slashes, so both are tried.
    """
    from . import worktree

    try:
        main = str(worktree.main_checkout(ctx))
    except Exception:  # noqa: BLE001 - an unlistable clone still has the tree it is in
        main = str(ctx.worktree_root)
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    for key in (re.sub(r"[^A-Za-z0-9]", "-", main), main.replace("/", "-")):
        index = projects / key / "memory" / "MEMORY.md"
        if index.is_file():
            return index
    return None


def memory_overflow(ctx: GitContext) -> str:
    """One line when MEMORY.md is past what Claude Code loads, and "" otherwise."""
    index = memory_index(ctx)
    if index is None:
        return ""
    raw = index.read_bytes() if index.is_file() else b""
    lines = raw.count(b"\n")
    if len(raw) <= MEMORY_LIMIT_BYTES and lines <= MEMORY_LIMIT_LINES:
        return ""
    return (f"MEMORY.md is {len(raw):,} bytes, {lines} lines: Claude Code loads the first "
            f"{MEMORY_LIMIT_LINES} lines or {MEMORY_LIMIT_BYTES // 1000} KB, so the rest of the "
            "index reaches no session")


# --- checking a layer -------------------------------------------------------------------------

_REFERENCE = re.compile(r"(\.claude/rules/[\w./-]+\.md)(?:`)?(?:\s*→\s*«([^»]+)»)?")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def _tracked(ctx: GitContext) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(ctx.worktree_root), "ls-files", "-z"],
                             capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [name for name in out.decode("utf-8", "replace").split("\0") if name]


def _instruction_files(root: Path) -> list[Path]:
    found = [root / name for name in ("CLAUDE.md", "CLAUDE.local.md") if (root / name).is_file()]
    for folder in (RULES_DIR, ".claude/skills", ".claude/commands", ".claude/agents"):
        directory = root / folder
        if directory.is_dir():
            found += sorted(p for p in store.files_under(directory) if p.name.endswith(".md"))
    return found


def problems(ctx: GitContext) -> list[str]:
    """What is wrong with the scoping as it stands: each finding is a fix someone can make."""
    tracked = _tracked(ctx)
    out = [said for rule in rules(ctx) for said in _rule_problems(rule, tracked)]
    root = ctx.worktree_root
    for path in _instruction_files(root):
        out += _reference_problems(root, path)
    return out


def _rule_problems(rule: Rule, tracked: list[str]) -> list[str]:
    out = []
    if rule.paths and len(_read(rule.path)) > LOAD_LIMIT_CHARS:
        out.append(f"{rule.shown}: {len(_read(rule.path)):,} characters; Claude Code truncates a "
                   f"scoped rule past {LOAD_LIMIT_CHARS:,}. Split it.")
    if tracked and not rule.shown.startswith("~"):
        out += [f"{rule.shown}: `paths:` entry `{pattern}` matches no tracked file, so this part "
                "of the rule never loads (Claude Code matches a rule's `paths:` from the "
                "project root)." for pattern in rule.paths
                if not any(glob_regex(pattern).fullmatch(name) for name in tracked)]
    return out + _command_problems(rule)


def _command_problems(rule: Rule) -> list[str]:
    out = []
    if rule.commands and not rule.paths:
        out.append(f"{rule.shown}: `commands:` without `paths:` scopes nothing; the rule "
                   "loads at every launch. Add the `paths:` it is about.")
    for pattern in rule.commands:
        try:
            re.compile(pattern)
        except re.error as error:
            out.append(f"{rule.shown}: `commands:` entry `{pattern}` is not a regular "
                       f"expression ({error}); it is matched as plain words.")
    return out


def _reference_problems(root: Path, path: Path) -> list[str]:
    """Each `.claude/rules/x.md` an instruction file names that is gone, and each
    `→ «heading»` after one that the file it names does not have."""
    out = []
    shown = path.relative_to(root).as_posix()
    for match in _REFERENCE.finditer(_read(path)):
        target = root / match.group(1)
        if not target.is_file():
            out.append(f"{shown}: names `{match.group(1)}`, which is not there.")
        elif match.group(2) and not _has_heading(target, match.group(2)):
            out.append(f"{shown}: points to «{match.group(2)}» in `{match.group(1)}`, which "
                       "has no such heading.")
    return out


def _has_heading(target: Path, wanted: str) -> bool:
    # A reference wraps where its line does; a heading never does.
    wanted = " ".join(wanted.split()).lower()
    return any(wanted in " ".join(h.split()).lower() for h in _HEADING.findall(_read(target)))


def _git_show(ctx: GitContext, rev: str, name: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ctx.worktree_root), "show", f"{rev}:{name}"],
                              capture_output=True, check=True, timeout=30).stdout.decode(
            "utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def _files_at(ctx: GitContext, rev: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(ctx.worktree_root), "ls-tree", "-r", "-z",
                              "--name-only", rev], capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
    keep = ("CLAUDE.md", "CLAUDE.local.md")
    return [n for n in names if n in keep or (n.endswith(".md") and n.startswith(
        (RULES_DIR + "/", ".claude/skills/", ".claude/commands/", ".claude/agents/")))]


def _lines_of(text: str) -> list[tuple[str, str]]:
    """(line, the heading it sits under) for every line of substance, frontmatter left out."""
    body = text
    if text.startswith("---"):
        parts = text.split("\n---", 1)
        body = parts[1].split("\n", 1)[1] if len(parts) == 2 and "\n" in parts[1] else ""
    heading, out = "", []
    for raw in body.splitlines():
        line = raw.strip()
        found = _HEADING.match(line)
        if found:
            heading = found.group(1).strip()
        if len(line) < 3 or set(line) <= set("-*_=|:# `"):
            continue
        out.append((line, heading))
    return out


def _scoped_text(text: str, name: str) -> bool:
    if name.startswith(".claude/skills/") or name.startswith(".claude/commands/") or \
            name.startswith(".claude/agents/"):
        return True
    return bool(field(text, "paths"))


@dataclass
class Moves:
    dropped: list[tuple[str, str]]
    scope_changed: list[tuple[str, str, str, bool]]
    lost_heading: list[tuple[str, str, str]]


def compare(ctx: GitContext, since: str) -> Moves:
    """Every line of the instruction layer at `since`, found again now, or named.

    Dropped: a line that is in no instruction file any more. Scope changed: a line that
    loaded at every launch and now loads only beside files or as a skill — flagged when it
    reads as meant for every session. Lost heading: a line now in another file whose heading
    is not there, so it reads without the context it was written under.
    """
    now: dict[str, list[tuple[str, str, bool]]] = {}
    root = ctx.worktree_root
    for path in _instruction_files(root):
        name = path.relative_to(root).as_posix()
        text = _read(path)
        scoped = _scoped_text(text, name)
        for line, heading in _lines_of(text):
            now.setdefault(line, []).append((name, heading, scoped))
    moves = Moves([], [], [])
    for name in _files_at(ctx, since):
        text = _git_show(ctx, since, name)
        was_scoped = _scoped_text(text, name)
        for line, heading in _lines_of(text):
            _place(moves, root, name, was_scoped, line, heading, now.get(line))
    return moves


def _place(moves: Moves, root: Path, name: str, was_scoped: bool, line: str, heading: str,
           places: list[tuple[str, str, bool]] | None) -> None:
    """Where one line of the old layer went: nowhere, its own file, or another one."""
    if not places:
        moves.dropped.append((name, line))
        return
    if not was_scoped and all(scoped for _n, _h, scoped in places):
        # The same file counts: a rule that gained `paths:` keeps every line where it was,
        # and every one of them stopped loading at launch.
        moves.scope_changed.append((name, places[0][0], line, bool(_EVERYWHERE.search(line))))
    if any(place == name for place, _h, _s in places):
        return
    if heading and not any(heading in _read(root / place) for place, _h, _s in places):
        moves.lost_heading.append((name, places[0][0], line))


def cost(ctx: GitContext) -> tuple[int, list[tuple[str, int]]]:
    """Bytes loaded at every launch, project and user, and the files largest first."""
    from . import knowledge

    files = [(path.relative_to(ctx.worktree_root).as_posix(), path.stat().st_size)
             for path in knowledge.at_launch(ctx) if path.is_file()]
    files += [(rule.shown, rule.path.stat().st_size) for rule in rules(ctx)
              if rule.shown.startswith("~") and not rule.paths and rule.path.is_file()]
    index = memory_index(ctx)
    if index is not None:
        size = min(index.stat().st_size, MEMORY_LIMIT_BYTES)
        files.append(("MEMORY.md (auto memory, as loaded)", size))
    files.sort(key=lambda item: -item[1])
    return sum(size for _name, size in files), files


# --- proving it loads -------------------------------------------------------------------------

PROBE_PROMPT = "Read the file {file} with the Read tool, then reply with the single word: done"
PROBE_TIMEOUT = 240


def _transcript(cwd: Path, session: str) -> Path | None:
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    for key in (re.sub(r"[^A-Za-z0-9]", "-", str(cwd)), str(cwd).replace("/", "-")):
        found = projects / key / f"{session}.jsonl"
        if found.is_file():
            return found
    hits = list(projects.glob(f"*/{session}.jsonl"))
    return hits[0] if hits else None


def _probed(rule: Rule, only: str) -> bool:
    if rule.shown.startswith("~") or not rule.paths:
        return False
    return only in rule.shown if only else "/decisions/" not in rule.shown


def _probe_one(ctx: GitContext, rule: Rule, target: str, claude: str) -> str:
    """One headless session reads `target`; whether `rule` reached it."""
    try:
        ran = subprocess.run(
            [claude, "-p", PROBE_PROMPT.format(file=target), "--output-format", "json",
             "--allowedTools", "Read", "--max-turns", "3"],
            cwd=ctx.worktree_root, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except FileNotFoundError:
        return f"`{claude}` is not on PATH"
    except subprocess.TimeoutExpired:
        return f"no answer in {PROBE_TIMEOUT} s"
    session = _session_of(ran.stdout)
    transcript = _transcript(ctx.worktree_root, session) if session else None
    if transcript is None:
        return "the session left no transcript to read"
    seen = str(rule.path) in transcript.read_text(encoding="utf-8", errors="replace")
    return "loaded" if seen else "not loaded"


def _session_of(stdout: str) -> str:
    try:
        answer = json.loads(stdout)
    except ValueError:
        return ""
    return str(answer.get("session_id") or "") if isinstance(answer, dict) else ""


def probe(ctx: GitContext, only: str = "", claude: str = "claude") -> list[tuple[str, str, str]]:
    """Ask a real headless session to read one file per scoped rule, and look in its transcript
    for the rule's text having been attached: (rule, file, "loaded" / "not loaded" / why not).

    The one check that does not take Claude Code's loading on trust. A rule whose globs match
    nothing tracked is named here as such, as `problems` names it. This plugin's own decision
    records are left out unless named: there are a hundred of them, and `make knowledge`
    holds their paths.
    """
    tracked = _tracked(ctx)
    out: list[tuple[str, str, str]] = []
    for rule in rules(ctx):
        if not _probed(rule, only):
            continue
        target = next((name for name in tracked if matches(rule, name)), "")
        verdict = _probe_one(ctx, rule, target, claude) if target else \
            "no tracked file matches its paths"
        out.append((rule.shown, target, verdict))
        if verdict.endswith("is not on PATH"):
            return out
    return out
