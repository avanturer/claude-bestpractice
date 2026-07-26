"""Project stage, inferred from the repository. Never a setting the founder maintains.

No surveyed tool does this: every one applies a fixed ceremony whether the repo is a
three-day prototype or a revenue system. The consequence is that heavy methodologies
get abandoned on small projects and light ones ship untested payment code.

Signals are cheap, purely local, and each one is a fact rather than a judgement. The
ratchet only tightens: a stage reached is recorded, and a later probe that would lower
it is ignored. Deleting a CI file must not silently disable the gates it turned on.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .gitctx import GitContext

PROTOTYPE = "prototype"
TRACTION = "traction"
REVENUE = "revenue"

ORDER = {PROTOTYPE: 0, TRACTION: 1, REVENUE: 2}
STAGE_FILE = "stage.json"

_PAYMENT_DEPS = (
    "stripe",
    "paddle",
    "lemonsqueezy",
    "braintree",
    "adyen",
    "razorpay",
    "paypal",
)
_AUTH_DEPS = ("next-auth", "authlib", "passport", "devise", "supabase", "clerk", "auth0", "firebase")
_LIVE_KEY = re.compile(r"\b(?:sk|pk|rk)_live_[A-Za-z0-9]{8,}")
_MIGRATION_DIRS = ("migrations", "db/migrate", "prisma/migrations", "alembic/versions")
_USER_TABLE = re.compile(r"(?i)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?[\"`']?(users?|accounts?|sessions?)\b")


@dataclass
class StageSignals:
    has_ci: bool = False
    has_deploy_config: bool = False
    has_migrations: bool = False
    has_user_table: bool = False
    has_auth_dep: bool = False
    has_payment_dep: bool = False
    has_live_key_shape: bool = False
    has_error_tracking: bool = False
    commits: int = 0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "has_ci": self.has_ci,
            "has_deploy_config": self.has_deploy_config,
            "has_migrations": self.has_migrations,
            "has_user_table": self.has_user_table,
            "has_auth_dep": self.has_auth_dep,
            "has_payment_dep": self.has_payment_dep,
            "has_live_key_shape": self.has_live_key_shape,
            "has_error_tracking": self.has_error_tracking,
            "commits": self.commits,
            "reasons": self.reasons,
        }


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _manifest_text(root: Path) -> str:
    chunks = []
    for name in ("package.json", "requirements.txt", "pyproject.toml", "Gemfile", "go.mod", "Cargo.toml"):
        p = root / name
        if p.exists():
            chunks.append(_read(p).lower())
    return "\n".join(chunks)


def probe(ctx: GitContext) -> StageSignals:
    root = ctx.worktree_root
    sig = StageSignals()

    if (root / ".github" / "workflows").is_dir() or (root / ".gitlab-ci.yml").exists():
        sig.has_ci = True
        sig.reasons.append("CI config present")

    for name in ("vercel.json", "fly.toml", "railway.json", "Procfile", "Dockerfile", "render.yaml"):
        if (root / name).exists():
            sig.has_deploy_config = True
            sig.reasons.append(f"deploy config {name}")
            break

    migration_files: list[Path] = []
    for rel in _MIGRATION_DIRS:
        d = root / rel
        if d.is_dir():
            sig.has_migrations = True
            migration_files.extend([p for p in d.rglob("*") if p.is_file()][:200])
    if sig.has_migrations:
        sig.reasons.append("migrations directory present")

    for path in migration_files[:200]:
        if _USER_TABLE.search(_read(path, 40_000)):
            sig.has_user_table = True
            sig.reasons.append(f"user-shaped table in {path.name}")
            break

    manifest = _manifest_text(root)
    if any(dep in manifest for dep in _AUTH_DEPS):
        sig.has_auth_dep = True
        sig.reasons.append("auth dependency in manifest")
    if any(dep in manifest for dep in _PAYMENT_DEPS):
        sig.has_payment_dep = True
        sig.reasons.append("payment dependency in manifest")
    if "sentry" in manifest or "glitchtip" in manifest or "bugsnag" in manifest:
        sig.has_error_tracking = True
        sig.reasons.append("error tracking in manifest")

    # Committed config only. Scanning the whole tree would read node_modules and would
    # itself become a way to surface secrets.
    for name in (".env.example", "app.json", "vercel.json", "fly.toml", "render.yaml"):
        p = root / name
        if p.exists() and _LIVE_KEY.search(_read(p, 40_000)):
            sig.has_live_key_shape = True
            sig.reasons.append(f"live-mode key shape in {name}")
            break

    from .gitctx import _run

    count = _run(["rev-list", "--count", "HEAD"], root, check=False)
    sig.commits = int(count) if count.isdigit() else 0
    return sig


def classify(sig: StageSignals) -> str:
    if sig.has_payment_dep or sig.has_live_key_shape:
        return REVENUE
    if sig.has_user_table or sig.has_auth_dep:
        return TRACTION
    if sig.has_ci and sig.has_deploy_config:
        return TRACTION
    return PROTOTYPE


def current(ctx: GitContext, override: str | None = None) -> tuple[str, StageSignals]:
    """Resolve the stage, applying the ratchet so it never regresses.

    An override is honoured upward but not downward: the point of the ratchet is that
    a deleted CI file cannot silently switch off the gates it enabled.
    """
    sig = probe(ctx)
    detected = classify(sig)
    if override and override in ORDER:
        detected = override if ORDER[override] > ORDER[detected] else detected

    path = store.tier_a(ctx, STAGE_FILE)
    recorded = store.read_json(path, default={}) or {}
    previous = recorded.get("stage") if isinstance(recorded, dict) else None

    resolved = detected
    if previous in ORDER and ORDER[previous] > ORDER[detected]:
        resolved = previous
        sig.reasons.append(f"ratchet held at {previous} (probe said {detected})")

    if resolved != previous:
        store.write_json(
            path,
            {
                "stage": resolved,
                "reached_at": time.time(),
                "signals": sig.to_dict(),
            },
            mode=0o644,
        )
    return resolved, sig


def gates_for(stage: str) -> dict[str, bool]:
    """Which gates fire at this stage. The prototype row is deliberately short."""
    return {
        # Always. The spine does not scale down: a prototype that lies about passing
        # tests is exactly as useless as a revenue system that does.
        "evidence_gate": True,
        "scope_drift": True,
        "loop_detect": True,
        "secret_scan": True,
        # Prototype explicitly turns this OFF: no back-compat shims while nothing
        # consumes the code.
        "forbid_compat_shims": stage == PROTOTYPE,
        "clean_rerun": ORDER[stage] >= ORDER[TRACTION],
        "migration_gate": ORDER[stage] >= ORDER[TRACTION],
        "worktree_db_isolation": ORDER[stage] >= ORDER[TRACTION],
    }

# Deliberately absent: `triple_run_critical` and `backup_restore_check` were declared
# here and read by nothing, while three READMEs promised them. A flag no consumer reads
# is not a feature behind a switch, it is a claim — and an unimplemented claim in a
# project whose thesis is "verify, do not assert" is the worst kind of bug it can have.
