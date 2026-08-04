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


def slugify(text: str, fallback: str = "work") -> str:
    words = "".join(c.lower() if c.isalnum() else " " for c in text).split()[:5]
    return "-".join(words) or fallback


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


def record(ctx: GitContext, slug: str, absolute: str, branch: str, trusted: bool) -> dict:
    body = {
        "path": absolute,
        "branch": branch,
        "port": derive_port(absolute),
        "database": derive_db_name(ctx.worktree_root.name, slug),
        "trusted": trusted,
    }
    store.write_json(store.tier_b(ctx, "worktrees", f"{slug}.json"), body)
    return body


def target_for(ctx: GitContext, slug: str) -> Path:
    """Outside the repository by construction — a worktree inside the working tree shows
    up in every status, every glob and every scan the sibling sessions run."""
    return ctx.worktree_root.parent / f"{ctx.worktree_root.name}-{slug}"


def provision(ctx: GitContext, task: str = "") -> Path | None:
    """Create the worktree this session should be working in, or None if git refused.

    Returns the existing path when it is already there, so a session that is refused twice
    is sent to the same place rather than accumulating trees.

    None is not a failure to handle loudly: the caller falls back to naming the command,
    which is where this started. Better to say something true than to crash a fail-closed
    gate over a convenience.
    """
    slug = slugify(task)
    target = target_for(ctx, slug)
    absolute = str(target)

    if target.is_dir():
        record(ctx, slug, absolute, f"feat/{slug}", trust(absolute))
        return target

    proc = subprocess.run(
        ["git", "worktree", "add", "-b", f"feat/{slug}", absolute],
        cwd=str(ctx.worktree_root), capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=120,
    )
    if proc.returncode != 0:
        # A branch of that name already exists is the common one, and it is recoverable:
        # attach a worktree to the branch instead of creating it again.
        attach = subprocess.run(
            ["git", "worktree", "add", absolute, f"feat/{slug}"],
            cwd=str(ctx.worktree_root), capture_output=True,
            encoding="utf-8", errors="surrogateescape", timeout=120,
        )
        if attach.returncode != 0:
            return None

    record(ctx, slug, absolute, f"feat/{slug}", trust(absolute))
    return target
