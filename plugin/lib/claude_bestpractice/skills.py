"""What a skill says about itself, and whether what it says is still current.

A skill is advice the model chooses to read, and some of it is a verdict about the outside
world: a design trend, a platform's guidance, a library's API. Such a verdict ages without
the file changing. `landing-not-slop` said glassmorphism "dates a page instantly" a year
after Apple made Liquid Glass the navigation layer of every one of its platforms, and a
session following it would have pushed an app against its platform (#365).

So a skill carries the date it was last checked (`metadata.reviewed`) and, where it rests
on outside facts, how long that check holds (`metadata.recheck-after-days`). `make check`
holds this plugin's own skills to the format; `claude-bp status` names one past its date.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import store
from .gitctx import quiet_env

SHIPPED = Path(__file__).resolve().parent.parent.parent / "skills"

REVIEWED = "metadata.reviewed"
RECHECK = "metadata.recheck-after-days"

# A skill copied from another repository into a pack says where from, at which commit, and
# under which licence; the licence's text sits beside it as `LICENSE`. A copy that drifts from
# its upstream, or that the upstream's licence never allowed, is a skill nobody can vouch for.
UPSTREAM = "metadata.upstream"
COMMIT = "metadata.commit"
LICENSE = "metadata.license"
# Licences that let a copy ship changed, its text kept beside it. A skill under any other, a
# source-available one among them, is linked to, never copied.
REDISTRIBUTABLE = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD",
                             "CC0-1.0", "CC-BY-4.0", "Unlicense"})
_UPSTREAM = re.compile(r"^github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/(?P<path>\S+?)/?$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.-]+):\s*(?P<value>.*)$")
_BLOCK_SCALARS = {"|", ">", "|-", ">-", "|+", ">+"}


@dataclass(frozen=True)
class Skill:
    name: str
    path: Path
    reviewed: datetime.date | None
    recheck_after_days: int

    def due(self) -> datetime.date | None:
        """The day this skill's outside facts are owed a fresh look; None for one that rests on
        none, or that never said when it was checked."""
        if self.reviewed is None or self.recheck_after_days <= 0:
            return None
        return self.reviewed + datetime.timedelta(days=self.recheck_after_days)


def meta(text: str) -> dict[str, str]:
    """A `SKILL.md`'s frontmatter as flat strings, a key one level under a map named
    `<map>.<key>`: `metadata.reviewed`.

    The subset of YAML skills are written in, read without a YAML library (the plugin is
    standard library only): scalars, quoted or not, and one level of nesting. A list or a
    block scalar is skipped, not misread.
    """
    head, sep, _ = text[3:].partition("\n---") if text.startswith("---") else ("", "", "")
    out: dict[str, str] = {}
    parent = ""
    for found in filter(None, map(_KEY.match, head.splitlines() if sep else [])):
        parent, named, value = _entry(found, parent)
        if named:
            out[named] = value
    return out


def _entry(found: re.Match[str], parent: str) -> tuple[str, str, str]:
    """One frontmatter line: the map open after it, the name it is filed under ("" for a line
    that files nothing), and its value."""
    key, value = found["key"], _unquoted(found["value"].strip())
    if value in _BLOCK_SCALARS:
        return "", "", ""
    if not found["indent"]:
        return ("", key, value) if value else (key, "", "")
    return (parent, f"{parent}.{key}", value) if parent and value else (parent, "", "")


def _unquoted(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read(path: Path) -> Skill | None:
    """The skill whose `SKILL.md` this is; None where the file cannot be read."""
    try:
        fields = meta(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return Skill(
        name=fields.get("name") or path.parent.name,
        path=path,
        reviewed=_date(fields.get(REVIEWED, "")),
        recheck_after_days=_days(fields.get(RECHECK, "")),
    )


def _date(value: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _days(value: str) -> int:
    return int(value) if value.isdigit() else 0


def ours() -> list[Skill]:
    """The skills this plugin ships, by directory name."""
    try:
        paths = sorted(SHIPPED.glob("*/SKILL.md"))
    except OSError:
        return []
    return [skill for skill in map(read, paths) if skill is not None]


def malformed(path: Path, today: datetime.date) -> list[str]:
    """What is wrong with how this shipped skill dates itself, one line each; empty when
    nothing is. A skill with no date cannot be told stale, and a date in the future never is."""
    try:
        fields = meta(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}: cannot be read ({exc})"]
    found: list[str] = []
    reviewed = _date(fields.get(REVIEWED, ""))
    if reviewed is None:
        found.append(f"{path}: `{REVIEWED}` is missing or not YYYY-MM-DD")
    elif reviewed > today:
        found.append(f"{path}: `{REVIEWED}` {reviewed} is in the future")
    if RECHECK in fields and _days(fields[RECHECK]) <= 0:
        found.append(f"{path}: `{RECHECK}` must be a whole number of days above 0")
    return found + _provenance(path, fields)


def _provenance(path: Path, fields: dict[str, str]) -> list[str]:
    """What a skill copied from elsewhere does not say about where it came from."""
    if UPSTREAM not in fields:
        return []
    found = [] if _UPSTREAM.match(fields[UPSTREAM]) else [
        f"{path}: `{UPSTREAM}` is github.com/<owner>/<repo>/<the skill's folder>"]
    if not _COMMIT_SHA.match(fields.get(COMMIT, "")):
        found.append(f"{path}: `{COMMIT}` is the full 40-character commit it was copied at")
    if fields.get(LICENSE) not in REDISTRIBUTABLE:
        found.append(f"{path}: `{LICENSE}` {fields.get(LICENSE) or 'is missing'}: a copy ships "
                     f"only under one of {', '.join(sorted(REDISTRIBUTABLE))}")
    if not (path.parent / "LICENSE").is_file():
        found.append(f"{path}: the upstream's licence text is not beside it as LICENSE")
    return found


def upstream(path: Path) -> tuple[str, str, str, str] | None:
    """(owner, repository, folder, commit) a copied skill came from; None for one of our own."""
    try:
        fields = meta(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    found = _UPSTREAM.match(fields.get(UPSTREAM, ""))
    if not found or not _COMMIT_SHA.match(fields.get(COMMIT, "")):
        return None
    return found["owner"], found["repo"], found["path"], fields[COMMIT]


def overdue(skills: list[Skill], today: datetime.date) -> list[Skill]:
    """The skills whose recheck date has passed, oldest first."""
    late = [skill for skill in skills if (skill.due() or today) < today]
    return sorted(late, key=lambda skill: skill.due() or today)


def overdue_line(today: datetime.date | None = None) -> str:
    """`skills due for a fresh look: <name> (reviewed …, due …), …`, or "" when none are."""
    late = overdue(ours(), today or datetime.date.today())
    if not late:
        return ""
    listed = ", ".join(f"{skill.name} (reviewed {skill.reviewed}, due {skill.due()})"
                       for skill in late)
    return (f"skills due for a fresh look: {listed}. Their dated verdicts (marked †) are "
            "re-checked against current sources before they are trusted.")


# Every skill a session in this repository can load, and where each comes from (#364).
#
# A founder's repository had half its hand-written skills unfollowable and three synced skills
# gone for four weeks before anyone asked. Nothing listed them: this plugin counted its own
# three. Read off disk: Claude Code's 2.1.325 API offers no listing that a hook or the hooks
# module can ask. What is not seen: a skill an MCP server serves, a
# plugin loaded with `--plugin-dir` or from a marketplace added by local path, and a skill a
# managed policy installs.

PROJECT, USER, SYNCED, PLUGIN = "project", "user", "synced", "plugin"

# Skills this plugin shipped and moved into a pack, by their old bare name: the pack's id. A
# repository that read one is told once where it went and the one command that brings it back;
# a pack enabled there lists it as `<pack>:<name>`, and it is not gone.
MOVED = {"landing-not-slop": "design@claude-bestpractice"}

# How Claude Code names a skill synced from a claude.ai account: "Synced skills need the
# namespace in rules: `Skill(anthropic-skills:pdf)`" (Claude Code's skills page).
SYNCED_NAMESPACE = "anthropic-skills"


@dataclass(frozen=True)
class Listed:
    """One loadable skill: its name as a session invokes it (`deploy`, `apps/web:deploy`,
    `anthropic-skills:pdf`, `bestpractice:llm-first-code`), its source kind, where it comes
    from (a directory, a synced account's last sync, `<plugin>@<marketplace>`), its file."""

    name: str
    kind: str
    origin: str
    path: Path


@dataclass(frozen=True)
class Account:
    """A claude.ai account's synced skills on this machine: its folder under
    `~/.claude/skills/synced/`, when Claude Code last synced it (epoch seconds), its names."""

    folder: Path
    synced_at: float
    names: tuple[str, ...]


def inventory(root: Path, home: Path | None = None) -> list[Listed]:
    """Every skill a session in the tree at `root` can load, by source."""
    home = home or Path.home()
    return [*_project(root), *_user(home), *_synced(home), *_plugins(root, home)]


def _project(root: Path) -> list[Listed]:
    """The repository's own: `.claude/skills/` and `.claude/commands/` at its root, and the
    directory-scoped `<dir>/.claude/skills/` git tracks, listed as `<dir>:<name>`."""
    found = _skills_in(root / ".claude" / "skills", "", PROJECT, ".claude/skills")
    found += _commands_in(root / ".claude" / "commands", "", PROJECT, ".claude/commands")
    for rel in _tracked(root, "*/.claude/skills/*/SKILL.md"):
        scope = rel.split("/.claude/skills/", 1)[0]
        path = root / rel
        found.append(Listed(f"{scope}:{path.parent.name}", PROJECT, f"{scope}/.claude/skills",
                            path))
    return found


def _user(home: Path) -> list[Listed]:
    skills_dir = home / ".claude" / "skills"
    return (_skills_in(skills_dir, "", USER, "~/.claude/skills")
            + _commands_in(home / ".claude" / "commands", "", USER, "~/.claude/commands"))


def _synced(home: Path) -> list[Listed]:
    """The skills of the account synced last: the one this machine's sessions load."""
    current = next(iter(accounts(home)), None)
    if current is None:
        return []
    when = datetime.date.fromtimestamp(current.synced_at) if current.synced_at else None
    origin = f"claude.ai account, synced {when}" if when else "claude.ai account"
    return [Listed(f"{SYNCED_NAMESPACE}:{name}", SYNCED, origin,
                   current.folder / name / "SKILL.md") for name in current.names]


def accounts(home: Path | None = None) -> list[Account]:
    """Each claude.ai account whose skills were synced to this machine, last synced first.

    Claude Code keeps one folder per account and writes a `manifest.json` in it: `lastUpdated`
    in milliseconds, and `skills` with each `name`. A sign-in as another account starts a new
    folder and leaves the old one as it was, its skills loaded by no session.
    """
    found: list[Account] = []
    try:
        folders = [path for path in ((home or Path.home()) / ".claude" / "skills"
                                     / "synced").iterdir() if path.is_dir()]
    except OSError:
        return []
    for folder in folders:
        manifest = store.read_json(folder / "manifest.json", default={})
        manifest = manifest if isinstance(manifest, dict) else {}
        listed = manifest.get("skills") if isinstance(manifest.get("skills"), list) else []
        names = tuple(str(skill.get("name")) for skill in listed
                      if isinstance(skill, dict) and skill.get("name"))
        stamp = manifest.get("lastUpdated")
        found.append(Account(folder, float(stamp) / 1000 if isinstance(stamp, (int, float))
                             else 0.0, names))
    return sorted(found, key=lambda account: account.synced_at, reverse=True)


def left_behind(home: Path | None = None) -> list[Account]:
    """Accounts synced before the current one whose skills it does not have: skills sessions
    loaded until the claude.ai account changed, and load no longer. Each lists only those."""
    every = accounts(home)
    if len(every) < 2:
        return []
    current = set(every[0].names)
    return [Account(account.folder, account.synced_at,
                    tuple(name for name in account.names if name not in current))
            for account in every[1:] if set(account.names) - current]


def _plugins(root: Path, home: Path) -> list[Listed]:
    """The skills and commands of each plugin the settings enable, from the copy Claude Code
    unpacked last: `<plugins>/cache/<marketplace>/<plugin>/<version>/`."""
    cache = Path(os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or home / ".claude" / "plugins")
    found: list[Listed] = []
    for plugin_id in enabled_plugins(root, home):
        name, _, marketplace = plugin_id.partition("@")
        copy = _unpacked(cache / "cache" / marketplace / name)
        if copy is not None:
            found += _skills_in(copy / "skills", f"{name}:", PLUGIN, plugin_id)
            found += _commands_in(copy / "commands", f"{name}:", PLUGIN, plugin_id)
    return found


def enabled_plugins(root: Path, home: Path) -> list[str]:
    """`<plugin>@<marketplace>` for each plugin `enabledPlugins` turns on: the user's settings,
    then the project's, then the project's local file, a later one overriding an earlier."""
    enabled: dict[str, bool] = {}
    for settings in (home / ".claude" / "settings.json", root / ".claude" / "settings.json",
                     root / ".claude" / "settings.local.json"):
        data = store.read_json(settings, default={})
        plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
        for plugin_id, on in (plugins.items() if isinstance(plugins, dict) else ()):
            if "@" in str(plugin_id) and isinstance(on, bool):
                enabled[str(plugin_id)] = on
    return sorted(plugin_id for plugin_id, on in enabled.items() if on)


def _unpacked(plugin_dir: Path) -> Path | None:
    """The newest version directory of a plugin in the cache that the CLI has not marked as
    superseded. Newest by time, since a version may be a commit's twelve characters."""
    try:
        copies = [path for path in plugin_dir.iterdir()
                  if path.is_dir() and not (path / ".orphaned_at").exists()]
    except OSError:
        return None
    return max(copies, key=lambda path: path.stat().st_mtime, default=None)


def _skills_in(folder: Path, prefix: str, kind: str, origin: str) -> list[Listed]:
    try:
        paths = sorted(path for path in folder.glob("*/SKILL.md") if path.parent.name != "synced")
    except OSError:
        return []
    return [Listed(f"{prefix}{path.parent.name}", kind, origin, path) for path in paths]


def _commands_in(folder: Path, prefix: str, kind: str, origin: str) -> list[Listed]:
    """A command's name is its file's, a subdirectory's name joined in front with `:`."""
    try:
        paths = sorted(folder.rglob("*.md"))
    except OSError:
        return []
    return [Listed(prefix + ":".join(path.relative_to(folder).with_suffix("").parts), kind,
                   origin, path) for path in paths]


def _tracked(root: Path, pattern: str) -> list[str]:
    try:
        done = subprocess.run(["git", "ls-files", "-z", "--", pattern], cwd=str(root),
                              capture_output=True, timeout=30, env=quiet_env())
    except (OSError, subprocess.TimeoutExpired):
        return []
    if done.returncode != 0:
        return []
    return [rel for rel in done.stdout.decode("utf-8", "surrogateescape").split("\0") if rel]
