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


@dataclass
class Config:
    test_command: list[str] = field(default_factory=list)
    artifact_globs: list[str] = field(default_factory=lambda: list(DEFAULT_ARTIFACT_GLOBS))
    clean_rerun: bool | None = None
    scope_drift_block: bool = True
    loop_detect: bool = True
    leases_enabled: bool = True
    lease_ttl_seconds: float = 1800.0
    max_tool_calls: int = 2_000
    max_repeat_signature: int = 3
    stage_override: str | None = None
    exempt_paths: list[str] = field(
        default_factory=lambda: [".claude/", "docs/", "README.md", "CHANGELOG.md"]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_command": self.test_command,
            "artifact_globs": self.artifact_globs,
            "clean_rerun": self.clean_rerun,
            "scope_drift_block": self.scope_drift_block,
            "loop_detect": self.loop_detect,
            "leases_enabled": self.leases_enabled,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_repeat_signature": self.max_repeat_signature,
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
    for candidate in ("tests", "test"):
        if (root / candidate).is_dir():
            return True
    return any(root.glob("test_*.py")) or any(root.glob("*_test.py"))


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
    "clean_rerun": bool,
    "scope_drift_block": bool,
    "loop_detect": bool,
    "leases_enabled": bool,
    "lease_ttl_seconds": float,
    "max_tool_calls": int,
    "max_repeat_signature": int,
    "stage_override": str,
    "exempt_paths": list,
}

_BOOL_WORDS = {"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False}


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
