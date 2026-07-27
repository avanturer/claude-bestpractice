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

# The ratchet is committed as one file per stage REACHED, holding a constant. That shape
# is not tidiness, it is merge behaviour, and it is decision 0001 applied to the one place
# that had ignored it.
#
# A single `stage.json` carried `reached_at` and the full signal dump, so every branch
# rewrote every byte of it. Two branches that both merely ran a gate — no stage change,
# nothing anyone did on purpose — came back with a conflict in a file the founder has
# never heard of, in a workflow whose entire premise is that they do not read code. The
# first merge of two parallel sessions was the trigger, which is the normal case here.
#
# Same stage on both sides now means the identical path with identical bytes, and git
# resolves add/add silently when the content matches. Different stages mean two different
# files, both survive the merge, and `current()` takes the highest — which is precisely
# what a ratchet means. The conflict is gone by construction rather than by a merge driver
# the founder would have to install.
STAGE_DIR = "stage"

# Volatile by nature and derived from the tree, so it is Tier B: never committed, never
# merged, rebuilt by `reindex` from the same probe that wrote it.
SIGNALS_FILE = "stage-signals.json"

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


def _reached_path(ctx: GitContext, stage: str) -> Path:
    return store.tier_a(ctx, STAGE_DIR, f"reached-{stage}.json")


def recorded_stage(ctx: GitContext) -> str | None:
    """The highest stage this branch has ever reached, or None.

    No reader for the older single-file shape, deliberately. Nothing has been released,
    so there is no installation to stay compatible with — and a compat path for a format
    that never shipped is the exact thing `check_slop.py` refuses, from the project that
    wrote the rule.
    """
    reached = [name for name in ORDER if _reached_path(ctx, name).exists()]
    return max(reached, key=lambda name: ORDER[name]) if reached else None


def current(ctx: GitContext, override: str | None = None) -> tuple[str, StageSignals]:
    """Resolve the stage, applying the ratchet so it never regresses.

    An override is honoured upward but not downward: the point of the ratchet is that
    a deleted CI file cannot silently switch off the gates it enabled.
    """
    sig = probe(ctx)
    detected = classify(sig)
    if override and override in ORDER:
        detected = override if ORDER[override] > ORDER[detected] else detected

    previous = recorded_stage(ctx)
    resolved = detected
    if previous in ORDER and ORDER[previous] > ORDER[detected]:
        resolved = previous
        sig.reasons.append(f"ratchet held at {previous} (probe said {detected})")

    marker = _reached_path(ctx, resolved)
    if not marker.exists():
        # A constant, and deliberately so: anything varying here — a timestamp, a host,
        # a signal dump — reintroduces the conflict this shape exists to remove.
        store.write_json(marker, {"stage": resolved}, mode=0o644)

    store.write_json(
        store.tier_b(ctx, SIGNALS_FILE),
        {"stage": resolved, "observed_at": time.time(), "signals": sig.to_dict()},
    )
    return resolved, sig


def gates_for(stage: str) -> dict[str, bool]:
    """Which gates this stage governs. Every key here is read by a gate; see below.

    The prototype row is deliberately short, and nothing in the table ever switches off
    as the stage rises — the founder's most valuable repository must not be the least
    protected one.
    """
    return {
        # Always. The spine does not scale down: a prototype that lies about passing
        # tests is exactly as useless as a revenue system that does.
        "evidence_gate": True,
        "scope_drift": True,
        "clean_rerun": ORDER[stage] >= ORDER[TRACTION],
        "migration_gate": ORDER[stage] >= ORDER[TRACTION],
    }


# Deliberately absent, and this list is the point of the table rather than a footnote to
# it. `triple_run_critical`, `backup_restore_check`, `forbid_compat_shims`,
# `worktree_db_isolation` and `secret_scan` were all declared here and read by nothing.
# A flag no consumer reads is not a feature behind a switch, it is a claim — and an
# unimplemented claim in a project whose thesis is "verify, do not assert" is the worst
# kind of bug it can have. Three of those five were promised by name in three READMEs.
#
# The last two are subtler and worth naming separately, because they were true:
# the credential pre-write scan and the loop detector really do fire at every stage.
# But this table did not make them fire — `pre-tool` runs the scan unconditionally and
# reads the loop switch from `config`. A row that merely agrees with behaviour it does
# not control is a second source of truth, and the moment the two disagree the table is
# the one that gets believed. `test_every_declared_gate_has_a_consumer` keeps it empty.
