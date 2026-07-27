"""Secret redaction for anything this plugin persists or injects.

Scope note: this is not a replacement for gitleaks on the commit path. It exists
because the plugin itself writes checkpoints and session boards, and the transcript
directory is an unencrypted leak path that `.gitignore` does not cover and key
rotation does not clean. Anything we write must already be scrubbed.

Detection is deliberately conservative: a missed secret is worse than a redacted
false positive, and none of these strings are ever needed by a reader.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("private-key-block", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    # Assignment forms catch the long tail: FOO_TOKEN=..., "password": "..."
    # The optional quote before the separator matters: JSON keys are quoted, so
    # `"api_key": "..."` has a `"` between the name and the colon.
    (
        "assigned-secret",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|APIKEY|API_KEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*)"
            r"[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{8,})"
        ),
    ),
    # Connection strings leak credentials in the authority component.
    ("url-credentials", re.compile(r"\b([a-z][a-z0-9+.\-]*)://[^\s:/@]+:[^\s:/@]+@")),
]

REDACTED = "[REDACTED]"

# A secret-shaped NAME assigned a reference rather than a literal is the correct
# pattern. Flagging it would punish the exact fix the gate tells the agent to apply,
# and an agent that gets denied for doing the right thing learns to route around us.
_INDIRECTION = re.compile(
    r"(?i)^(?:os\.environ|os\.getenv|process\.env|import\.meta\.env|env\[|ENV\[|getenv|"
    r"System\.getenv|std::env|Deno\.env|config\.|settings\.|secrets\.|\$\{?[A-Z_]|"
    r"<[A-Z_ ]+>|\.\.\.|xxx|placeholder|changeme|your[-_])"
)


def _is_indirection(value: str) -> bool:
    return bool(_INDIRECTION.match(value.strip()))


def scrub(text: str) -> str:
    """Return `text` with anything that looks like a credential replaced."""
    if not text:
        return text
    out = text
    for name, pattern in _PATTERNS:
        if name == "assigned-secret":
            out = pattern.sub(
                lambda m: m.group(0) if _is_indirection(m.group(2)) else f"{m.group(1)}={REDACTED}",
                out,
            )
        elif name == "url-credentials":
            out = pattern.sub(lambda m: f"{m.group(1)}://{REDACTED}@", out)
        else:
            out = pattern.sub(REDACTED, out)
    return out


def find(text: str) -> list[str]:
    """Names of the detectors that fired. Used by gates that must refuse a write."""
    hits: set[str] = set()
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text or ""):
            if name == "assigned-secret" and _is_indirection(match.group(2)):
                continue
            hits.add(name)
            break
    return sorted(hits)


def contains_secret(text: str) -> bool:
    return bool(find(text))


# The word boundary lives inside each branch: a branch ending in `:` is followed by a
# space, and `:` to ` ` is not a boundary, so a trailing \b would silently kill it.
_IMPERATIVE = re.compile(
    r"(?im)^\s*(?:"
    r"ignore\s+(?:all\s+)?previous\b"
    r"|disregard\s+(?:all\s+)?(?:previous|prior)\b"
    r"|you\s+are\s+now\b"
    r"|new\s+instructions?\b"
    r"|(?:system|assistant)\s*:"
    r"|execute\s+the\s+following\b"
    r"|run\s+this\s+command\b"
    r")"
)


def looks_like_injection(text: str) -> bool:
    """Heuristic flag for attacker-influenceable text.

    Explicitly NOT a defence — every published detection filter of this kind has been
    bypassed at high rates under adaptive attack. The actual defence is that this
    content is fenced as data and reaches the agent as a file it reads, never as an
    instruction. This flag only decides whether to mark a signal DEGRADED so it gets
    looked at.
    """
    return bool(_IMPERATIVE.search(text or ""))


def strip_control(text: str) -> str:
    """Remove control and zero-width characters used to hide payloads."""
    return "".join(
        ch
        for ch in (text or "")
        if ch in "\n\t" or (ch.isprintable() and ch not in "\u200b\u200c\u200d\u2060\ufeff")
    )
