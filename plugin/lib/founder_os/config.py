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


def load(ctx: GitContext) -> Config:
    raw = store.read_json(store.tier_a(ctx, CONFIG_NAME), default={}) or {}
    if not isinstance(raw, dict):
        raw = {}
    cfg = Config()
    for key in cfg.to_dict():
        if key in raw and raw[key] is not None:
            setattr(cfg, key, raw[key])
    if not cfg.test_command:
        cfg.test_command = detect_test_command(ctx.worktree_root)
    return cfg


def save(ctx: GitContext, cfg: Config) -> Path:
    path = store.tier_a(ctx, CONFIG_NAME)
    store.write_json(path, cfg.to_dict(), mode=0o644)
    return path
