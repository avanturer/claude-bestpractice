"""Whether a skill can be followed as written: what it points at exists, and its steps run.

A manual audit of one repository found half its hand-written skills could not be followed
(#364): a script sourced through `$CLAUDE_PROJECT_DIR`, which Claude Code sets for hook
commands and leaves empty in the Bash tool; an entry point and a section that had been
renamed; a stacked-PR command that merged in a loop, which this plugin's merge gate refuses.
Nothing had said so, because a skill is read by a model that does its best with what it finds.

Only what can be decided without running anything and without judgement is checked, in the
repository's own skills and the founder's (`skills.PROJECT`, `skills.USER`); a synced or a
plugin's skill is someone else's to fix:

- a script a shell step runs or sources, and a path written alone in backticks, exist;
- `make <target>` names a target of the repository's Makefile, `npm run <script>` (pnpm, bun)
  a script of its package.json;
- a shell step reads no variable Claude Code leaves out of the Bash tool's environment;
- the merge gate would take a shell step as written, and no step pushes to a trunk by name.

Whether a skill contradicts the repository's rules is a judgement, and a second model
watching the first is what this plugin does not ship (`.claude/rules/product.md`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import gitpolicy, pullrequest, shellcmd, skills, store
from .gitctx import GitContext

# Claude Code's plugin reference: these "aren't present in the environment of commands Claude
# runs through the Bash tool"; written `${…}` in a skill's content, it "substitutes the path
# inline when it loads the content". Measured empty in the Bash tool on 2.1.325. The project's
# is substituted in every skill, a plugin's only in that plugin's own.
_UNSET_IN_BASH = re.compile(r"\$(CLAUDE_PROJECT_DIR|CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA)\b")
_PLUGIN_ONLY = re.compile(r"\$\{(CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA)\}")
_PROJECT_ROOT = "${CLAUDE_PROJECT_DIR}/"

_FENCE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)")
_SHELL_LANGUAGES = {"bash", "sh", "shell", "zsh", "console", "shell-session"}
_INLINE = re.compile(r"`([^`\n]+)`")
_PATH = re.compile(r"^(?:\./)?[\w@.+-]+(?:/[\w@.+-]+)+/?$")
_INTERPRETERS = {"python", "python3", "node", "bash", "sh", "zsh", "ruby", "perl", "deno",
                 "bun", "tsx", "ts-node", "source", "."}
_PACKAGE_RUNNERS = {"npm", "pnpm", "bun"}


@dataclass(frozen=True)
class Finding:
    """One way a skill cannot be followed: the skill, its file and line, what is wrong."""

    skill: str
    path: Path
    line: int
    said: str

    def where(self, root: Path) -> str:
        try:
            shown = self.path.relative_to(root)
        except ValueError:
            shown = Path("~") / self.path.relative_to(Path.home()) \
                if self.path.is_relative_to(Path.home()) else self.path
        return f"{shown}:{self.line}"


def check(ctx: GitContext, listed: list[skills.Listed]) -> list[Finding]:
    """Every finding in the repository's and the founder's own skills, in file order."""
    root = ctx.worktree_root
    project = _Project(root, gitpolicy.default_branch(ctx))
    found: list[Finding] = []
    for item in listed:
        if item.kind in (skills.PROJECT, skills.USER):
            found += _check_file(item, project)
    return found


def shipped(ctx: GitContext, paths: list[Path]) -> list[Finding]:
    """Every finding in skills this plugin or one of its packs ships: a step the gates refuse,
    a variable the Bash tool lacks, and a link to a file of the skill's own that is not in its
    folder. A path, a script or a target a shipped skill names in a project is the project's to
    have, and is not looked for in this repository (#364, decision 0101)."""
    project = _Project(ctx.worktree_root, gitpolicy.default_branch(ctx))
    found: list[Finding] = []
    for path in paths:
        item = skills.Listed(path.parent.name, skills.PROJECT, "shipped", path)
        found += _check_file(item, project, own=path.parent)
    return found


class _Project:
    """What the repository offers a skill's steps: its files, Makefile targets, npm scripts."""

    def __init__(self, root: Path, default: str) -> None:
        self.root = root
        self.trunks = frozenset({*gitpolicy.TRUNK_NAMES, default} - {""})
        self.default = default
        self.targets = _make_targets(root / "Makefile")
        self.scripts = _npm_scripts(root / "package.json")


def _check_file(item: skills.Listed, project: _Project, own: Path | None = None) -> list[Finding]:
    """The findings in one skill's file; `own` is a shipped skill's folder, whose links are
    looked for there and whose project references are not looked for at all."""
    try:
        text = item.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    found: list[Finding] = []
    for number, line, shell in _lines(text):
        if shell:
            said = _step(line, project, references=own is None)
        else:
            said = _prose(line, project) if own is None else _own_links(line, own)
        found += [Finding(item.name, item.path, number, what) for what in said]
    return found


def _lines(text: str) -> list[tuple[int, str, bool]]:
    """Each line of prose or of a shell step with its number and whether it is a step, a step's
    continuation lines joined to the one they continue. A block in another language is skipped:
    its text is data, as a quoted word is."""
    out: list[tuple[int, str, bool]] = []
    pending, started = "", 0
    for number, line, language in _fenced(text):
        shell = language in _SHELL_LANGUAGES
        if language is not None and not shell:
            continue
        if shell and line.rstrip().endswith("\\"):
            pending, started = pending + line.rstrip()[:-1] + " ", started or number
            continue
        out.append((started or number, pending + line, shell))
        pending, started = "", 0
    return out


def _fenced(text: str) -> list[tuple[int, str, str | None]]:
    """Each line inside or outside a fenced block, with its number and the block's language:
    None outside every block, "" in one with no language named. The fences themselves go."""
    out: list[tuple[int, str, str | None]] = []
    fence, language = "", None
    for number, line in enumerate(text.splitlines(), 1):
        opened = _FENCE.match(line)
        if opened and (not fence or opened.group(1).startswith(fence)):
            fence, language = ("", None) if fence else (opened.group(1), opened.group(2).lower())
        else:
            out.append((number, line, language))
    return out


def _step(line: str, project: _Project, references: bool = True) -> list[str]:
    """What is wrong with one shell step; with `references` off, nothing it names in the
    project is looked for."""
    command = re.sub(r"^\s*\$\s+", "", line).strip()
    if not command or command.startswith("#"):
        return []
    found = [f"`${name}` is empty in the Bash tool: Claude Code sets it for hooks, and replaces "
             f"only the braced `${{{name}}}` when it loads a skill. Write it braced"
             for name in _UNSET_IN_BASH.findall(command)]
    found += [f"`${{{name}}}` is replaced only in a plugin's own skills, and set for no command "
              "a session runs" for name in _PLUGIN_ONLY.findall(command)]
    found += _refused(command, project)
    for argv in shellcmd.commands(command.replace(_PROJECT_ROOT, "")) if references else []:
        found += _references(argv, project)
    return found


def _refused(command: str, project: _Project) -> list[str]:
    """What this plugin's gates refuse in a step whoever runs it, wherever."""
    found: list[str] = []
    merge = pullrequest.merge_line("Bash", command)
    if merge:
        found.append("the merge gate refuses this step as written: " + merge.split("\n")[0]
                     .removeprefix("claude-bestpractice: "))
    for argv in shellcmd.runs(command, "git", "push"):
        trunk = gitpolicy.trunk_pushed(gitpolicy.split_git(argv)[2], "", project.trunks,
                                       project.default)
        if trunk:
            found.append(f"pushes to the trunk `{trunk}`, which the push gate refuses: open a "
                         "pull request instead (`claude-bp-ship --pr`)")
    return found


def _references(argv: list[str], project: _Project) -> list[str]:
    """A script, a make target or an npm script a command names that the repository lacks."""
    program = argv[0].rsplit("/", 1)[-1]
    if program in _INTERPRETERS:
        return _script_missing(argv[1:], project)
    if program == "make":
        return _targets_missing(argv[1:], project)
    if program in _PACKAGE_RUNNERS and len(argv) > 2 and argv[1] == "run":
        return _npm_script_missing(program, argv[2], project)
    return []


def _script_missing(words: list[str], project: _Project) -> list[str]:
    script = next((word for word in words if not word.startswith("-")), "")
    if _PATH.match(script) and "$" not in script and not (project.root / script).exists():
        return [f"runs `{script}`, which is not in the repository"]
    return []


def _targets_missing(words: list[str], project: _Project) -> list[str]:
    if project.targets is None:
        return ["runs `make`, and the repository has no Makefile at its root"]
    return [f"`make {target}`: the Makefile has no target `{target}`"
            for target in _make_goals(words) if target not in project.targets]


def _npm_script_missing(program: str, script: str, project: _Project) -> list[str]:
    if project.scripts is None or script in project.scripts:
        return []
    return [f"`{program} run {script}`: package.json has no script `{script}`"]


def _prose(line: str, project: _Project) -> list[str]:
    """A path a sentence names alone in backticks, as a file to read or run, that is gone."""
    found: list[str] = []
    for span in _INLINE.findall(line):
        candidate = span.strip()
        if _PATH.match(candidate) and not (project.root / candidate).exists() \
                and (project.root / candidate.split("/", 1)[0]).exists():
            found.append(f"names `{candidate}`, which is not in the repository")
    return found


# A link inside a skill to a file of its own: a relative path into its folder or out of it.
_OWN_LINK = re.compile(r"^(?:\./|\.\./|references/|scripts/|assets/)\S+$")
_MARKDOWN_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def _own_links(line: str, folder: Path) -> list[str]:
    """A shipped skill's link to one of its own files, in backticks or as a Markdown link,
    that is not in its folder: a copy that left a file of its upstream behind."""
    found: list[str] = []
    for link in [*map(str.strip, _INLINE.findall(line)), *_MARKDOWN_LINK.findall(line)]:
        if _OWN_LINK.match(link) and not (folder / link).exists():
            found.append(f"links `{link}`, which is not in the skill's folder")
    return found


def _make_goals(words: list[str]) -> list[str]:
    """The targets a `make` command asks for, past its options and their values."""
    goals: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in ("-C", "-f", "--file", "--directory", "-j", "-I", "-o", "-W"):
            skip = True
        elif not word.startswith("-") and "=" not in word:
            goals.append(word)
    return goals


def _make_targets(makefile: Path) -> set[str] | None:
    """The targets a Makefile defines; None where there is no Makefile."""
    try:
        text = makefile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found = set(re.findall(r"^([\w./-]+)\s*:(?!=)", text, re.M))
    for phony in re.findall(r"^\.PHONY\s*:(.*)$", text, re.M):
        found.update(phony.split())
    return found


def _npm_scripts(manifest: Path) -> set[str] | None:
    """The scripts a package.json defines; None where there is none."""
    if not manifest.is_file():
        return None
    data = store.read_json(manifest, default={})
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return set(scripts) if isinstance(scripts, dict) else set()


def lines(ctx: GitContext, findings: list[Finding]) -> list[str]:
    """Each finding as `<file>:<line> <skill>: <what>`, for `claude-bp skills`."""
    return [f"{finding.where(ctx.worktree_root)} {finding.skill}: {finding.said}"
            for finding in findings]


FILED = "skill-findings.json"


def file_cards(ctx: GitContext, findings: list[Finding]) -> list[str]:
    """One card per skill that cannot be followed, filed once for the findings it has now: the
    ids of the cards filed. A skill whose findings change after its card is filed gets another.

    Findings become cards rather than a line on a status nobody reads: the board is what the
    sessions decide their work from (#364).
    """
    from . import plan

    by_skill: dict[str, list[Finding]] = {}
    for finding in findings:
        by_skill.setdefault(finding.skill, []).append(finding)
    filed: list[str] = []
    with store.guarded_json(store.tier_b(ctx, FILED), default={}) as box:
        record = box[0] if isinstance(box[0], dict) else {}
        for skill, found in sorted(by_skill.items()):
            key = json.dumps(sorted(f"{f.where(ctx.worktree_root)} {f.said}" for f in found))
            if record.get(skill) == key:
                continue
            task = plan.add(
                ctx, f"Skill `{skill}` cannot be followed as written",
                body="\n".join(f"- {line}" for line in lines(ctx, found)),
                paths=sorted({_card_path(ctx, f.path) for f in found}),
                done_when="`claude-bp skills` names nothing in this skill",
                source="skillcheck",
            )
            record[skill] = key
            filed.append(task.id)
        box[0] = record
    return filed


def _card_path(ctx: GitContext, path: Path) -> str:
    try:
        return str(path.relative_to(ctx.worktree_root))
    except ValueError:
        return str(path)


def board_line(ctx: GitContext) -> str:
    """The session-start line naming the cards just filed for skills that cannot be followed,
    "" when none was: the findings were filed before, or there are none."""
    filed = file_cards(ctx, check(ctx, skills.inventory(ctx.worktree_root)))
    if not filed:
        return ""
    return (f"\nfiled {', '.join(filed)}: skills here that cannot be followed as written, each "
            "line named in the card. `claude-bp skills` lists them all.")
