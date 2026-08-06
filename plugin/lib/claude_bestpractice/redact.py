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
    ("url-credentials", re.compile(
        r"\b([a-z][a-z0-9+.\-]*)://(?P<user>[^\s:/@]+):(?P<secret>[^\s:/@]+)@(?P<host>[^\s:/@]+)")),
]

REDACTED = "[REDACTED]"

# A development default is not a secret. `postgres://app:app@localhost/app` has nothing to
# leak and nothing to rotate — and the gate refused the bug report ABOUT this false
# positive, twice, the second time with every component replaced by a placeholder, so the
# only way to describe it was prose (#75). A closed loop around a value that is safe by
# construction.
#
# Narrow on purpose: the host must be local AND the user must equal the password. A real
# credential that happens to point at localhost still has a distinct password, and a
# tunnelled production connection has both.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", "db", "postgres"}


# Words that ARE the placeholder. A comment documenting what an environment variable
# should contain names the parts rather than supplying them, and the rule fired on the
# documentation of the correct practice while the line under it read `os.getenv(...)` —
# the practice the rule exists to encourage (#80). Recognisable by the words alone.
_PLACEHOLDERS = {
    "user", "users", "username", "login", "pass", "password", "passwd", "pwd", "secret",
    "token", "key", "apikey", "api_key", "credential", "credentials", "youruser",
    "yourpassword", "myuser", "mypassword", "usuario", "senha",
    # Russian and transliterations, because a comment is written in the language of
    # whoever wrote it, and this one was.
    "\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c", "\u043b\u043e\u0433\u0438\u043d", "\u043f\u0430\u0440\u043e\u043b\u044c", "polzovatel", "logini", "parol",
}


def _is_placeholder(word: str) -> bool:
    return word.strip("<>[]{}()\u00ab\u00bb\"'").lower() in _PLACEHOLDERS


def _is_local_default(user: str, secret: str, host: str) -> bool:
    """A value with nothing to leak: a development default, or a documented shape.

    Both halves were reported as false positives that blocked the merge gate AND blocked
    the bug report describing them — the only way to file the second one was prose.
    """
    if _is_placeholder(user) or _is_placeholder(secret):
        return True
    return host.lower() in _LOCAL_HOSTS and user == secret

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
            out = pattern.sub(
                lambda m: m.group(0) if _is_local_default(
                    m.group("user"), m.group("secret"), m.group("host")
                ) else f"{m.group(1)}://{REDACTED}@{m.group('host')}",
                out,
            )
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
            # Skipped rather than broken out of: one development default early in a file
            # must not stop the scan before a real credential later in it.
            if name == "url-credentials" and _is_local_default(
                match.group("user"), match.group("secret"), match.group("host")
            ):
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


def locate(text: str) -> list[tuple[str, int, str]]:
    """(detector, line number, redacted excerpt) for each hit, so it can be FOUND.

    `find` returns names, and a name is not a location. A write was refused for "what looks
    like a credential", the founder deleted the two lines they suspected, the write was
    refused again, and there was no way to learn what had actually matched — so the file
    could not be written at all, by any route, and the only exit was to stop using the
    gate (#95). Their own diagnosis was wrong, which is the point: nothing told them.

    The excerpt is scrubbed before it is shown. Printing the matched value to prove a
    credential was matched would put it in the transcript, which is the thing being
    prevented.
    """
    out: list[tuple[str, int, str]] = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        for name in find(line):
            out.append((name, number, scrub(line).strip()[:120]))
    return out
