"""Decisions taken from compared options, not from the first idea that worked.

The failure is specific and it is not laziness. An agent asked to add caching adds
Redis, because Redis is what the training data says caching is. It works, the tests
pass, and nobody ever learns that the project had 200 requests a minute and a dict with
a TTL would have done it — with one fewer service to run, pay for and secure. The
decision was never made; a default was executed.

So the trigger is mechanical rather than a plea for thoughtfulness: **a new dependency
demands a comparison.** That is the moment a default gets executed, it is detectable
from the manifest diff, and it is the class of decision that is expensive to reverse —
a dependency is a permanent operational cost paid by every future session.

What is compared, and against what metrics, is chosen by whoever writes the record.
Fixing the metric list here would be worse than useless: the metrics that matter for a
queue are not the ones that matter for a CSS framework, and a fixed list gets filled in
ritually. What is enforced is that alternatives were named, scored on something stated,
and that the winner won on the numbers rather than on the write-up.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .gitctx import GitContext

OPTIONS_DIR = "options"
MIN_OPTIONS = 2

# Files whose change means a dependency was added or swapped.
MANIFESTS = (
    "package.json", "requirements.txt", "pyproject.toml", "Cargo.toml", "go.mod",
    "Gemfile", "composer.json", "pom.xml", "build.gradle", "pubspec.yaml",
)


@dataclass
class Option:
    name: str
    scores: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def total(self, weights: dict[str, float] | None = None) -> float:
        weights = weights or {}
        return sum(value * weights.get(metric, 1.0) for metric, value in self.scores.items())


@dataclass
class Comparison:
    id: str
    problem: str
    metrics: list[str]
    options: list[Option]
    chosen: str
    why: str
    subjects: list[str] = field(default_factory=list)
    recorded_at: float = 0.0
    path: Path | None = None

    def winner_on_numbers(self) -> str:
        """Whichever option scores highest — the check on the write-up matching reality."""
        if not self.options:
            return ""
        return max(self.options, key=lambda o: o.total()).name

    def is_consistent(self) -> tuple[bool, str]:
        """A comparison that names one winner and scores another is theatre."""
        if len(self.options) < MIN_OPTIONS:
            return False, f"only {len(self.options)} option(s); a comparison needs at least {MIN_OPTIONS}"
        if not self.metrics:
            return False, "no metrics named — 'better' with no axis is a preference"
        missing = [o.name for o in self.options if set(self.metrics) - set(o.scores)]
        if missing:
            return False, f"not scored on every metric: {', '.join(missing)}"
        if self.chosen not in {o.name for o in self.options}:
            return False, f"the chosen option {self.chosen!r} is not among the compared ones"
        best = self.winner_on_numbers()
        if best != self.chosen and not self.why.strip():
            return False, (
                f"{best!r} scores highest but {self.chosen!r} was chosen, with no reason given. "
                "Overriding the numbers is allowed; doing it silently is not."
            )
        return True, ""

    def line(self) -> str:
        names = ", ".join(f"{o.name}={o.total():.1f}" for o in self.options)
        return f"[{self.id}] {self.problem} → {self.chosen} ({names})"


def options_dir(ctx: GitContext) -> Path:
    return store.tier_a(ctx, OPTIONS_DIR)


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())[:6]) or "comparison"


def load_all(ctx: GitContext) -> list[Comparison]:
    directory = options_dir(ctx)
    if not directory.is_dir():
        return []
    loaded = (_from_json(path, store.read_json(path, default=None))
              for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.json")))
    return [c for c in loaded if c]


def _from_json(path: Path, raw: object) -> Comparison | None:
    if not isinstance(raw, dict) or not raw.get("problem"):
        return None
    return Comparison(
        id=path.name.split("-", 1)[0],
        problem=raw.get("problem", ""),
        metrics=list(raw.get("metrics") or []),
        options=[
            Option(o.get("name", ""), dict(o.get("scores") or {}), o.get("note", ""))
            for o in raw.get("options") or []
            if isinstance(o, dict)
        ],
        chosen=raw.get("chosen", ""),
        why=raw.get("why", ""),
        subjects=list(raw.get("subjects") or []),
        recorded_at=float(raw.get("recorded_at") or 0),
        path=path,
    )


def record(ctx: GitContext, comparison: Comparison) -> tuple[Comparison | None, str]:
    """File a comparison, or refuse it with the reason it is not one."""
    ok, complaint = comparison.is_consistent()
    if not ok:
        return None, complaint

    with store.file_lock(store.tier_b(ctx, "options-alloc.lock")):
        highest = max((int(c.id) for c in load_all(ctx) if c.id.isdigit()), default=0)
        comparison.id = f"{highest + 1:04d}"
        path = options_dir(ctx) / f"{comparison.id}-{_slug(comparison.problem)}.json"
        store.write_json(
            path,
            {
                "problem": comparison.problem,
                "metrics": comparison.metrics,
                "options": [
                    {"name": o.name, "scores": o.scores, "note": o.note} for o in comparison.options
                ],
                "chosen": comparison.chosen,
                "why": comparison.why,
                "subjects": comparison.subjects,
                "recorded_at": time.time(),
            },
            mode=0o644,
        )
    comparison.path = path
    return comparison, ""


def new_dependencies(ctx: GitContext, changed: list[str], baseline: str) -> list[str]:
    """Dependency names this turn added, read from the manifest diff.

    Names rather than "the manifest changed": bumping a version is not a decision, and a
    gate that fires on `npm audit fix` is one the founder switches off within a day.
    """
    added: list[str] = []
    for rel in changed:
        if Path(rel).name not in MANIFESTS:
            continue
        before = _dependency_names(_show(ctx, baseline, rel))
        after = _dependency_names((ctx.worktree_root / rel).read_text(encoding="utf-8", errors="replace")
                                  if (ctx.worktree_root / rel).is_file() else "")
        added.extend(sorted(after - before))
    return added


# Two shapes cover every manifest that matters: `"name": "^1.2"` (JSON, Gemfile, gradle)
# and `name>=1.2` inside one string (PEP 508, requirements.txt, go.mod).
# The name is optionally quoted: JSON quotes it, TOML (`serde = "1.0"`) does not.
_DEP_MAPPING = re.compile(r"""["']?([A-Za-z][\w.@/-]{1,60})["']?\s*[:=]\s*["'][\^~>=<\d*]""")
_DEP_REQUIREMENT = re.compile(r"""["']?([A-Za-z][\w.@/-]{1,60})\s*(?:[><=~!]=|[><]|\s+v?\d)""")

# Container keys and section headers, which look exactly like package names.
_NOT_PACKAGES = {
    "dependencies", "devdependencies", "peerdependencies", "optionaldependencies",
    "require", "requires", "scripts", "engines", "overrides", "resolutions",
    "dependency-groups", "project", "tool", "build-system", "module", "go", "python",
    "version", "name", "description", "license", "author", "main", "type",
}


def _dependency_names(text: str) -> set[str]:
    """Package names from any manifest format, without parsing five of them properly.

    Over-inclusive on purpose: a spurious name costs one comparison request, while a
    missed one is a dependency nobody ever compared — which is the whole failure.
    """
    if not text.strip():
        return set()
    names = {m.group(1) for m in _DEP_MAPPING.finditer(text)}
    names |= {m.group(1) for m in _DEP_REQUIREMENT.finditer(text)}
    return {
        n for n in names
        # `http`, not `http://`, ate the package `httpx` — a URL prefix has to include
        # its separator or it swallows every library whose name starts with the same
        # letters. Exactly the class of bug this whole project exists to catch.
        if n.lower() not in _NOT_PACKAGES and not n.startswith(("http://", "https://", "git+", "file:"))
    }


def _show(ctx: GitContext, ref: str, rel: str) -> str:
    import subprocess

    if not ref:
        return ""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel}"],
        cwd=str(ctx.worktree_root), capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=30,
    )
    return proc.stdout if proc.returncode == 0 else ""


def covered(ctx: GitContext, names: list[str]) -> set[str]:
    """Dependencies that already have a comparison naming them."""
    seen: set[str] = set()
    for comparison in load_all(ctx):
        text = json.dumps(
            [comparison.problem, comparison.chosen, [o.name for o in comparison.options]]
        ).lower()
        seen |= {name for name in names if name.lower() in text}
    return seen


def demand(ctx: GitContext, changed: list[str], baseline: str) -> str:
    """The refusal for a dependency added without comparing anything. Empty when fine."""
    added = new_dependencies(ctx, changed, baseline)
    uncompared = sorted(set(added) - covered(ctx, added))
    if not uncompared:
        return ""
    return (
        f"New dependencies with no comparison on record: {', '.join(uncompared[:6])}.\n"
        "A dependency is a permanent operational cost paid by every future session, and "
        "reaching for the obvious one is how it gets paid without anyone deciding to.\n"
        "Record what else was considered and on what numbers:\n"
        f"  founder-os-options add \"{uncompared[0]}: why this one\" \\\n"
        "      --metric latency --metric ops-burden --metric lock-in \\\n"
        f"      --option '{uncompared[0]}:8,4,3' --option 'stdlib:6,9,9' \\\n"
        f"      --chosen {uncompared[0]} --why \"...\"\n"
        "Set `compare_dependencies: false` in .claude/founder-os/config.json to stop asking."
    )
