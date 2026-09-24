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
    # The WHOLE block. Matching the BEGIN line alone redacted the one line that is not the
    # key and wrote the body and the END line into a signal file verbatim. To its END line
    # when the text has one within a key's length; otherwise the base64 that follows, which
    # is what is left of a key cut off mid-way.
    ("private-key-block", re.compile(
        r"-----BEGIN[A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
        r"(?:[\s\S]{0,16384}?-----END[A-Z ]*PRIVATE KEY(?: BLOCK)?-----|[A-Za-z0-9+/=\s]{0,16384})")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    # Assignment forms catch the long tail: FOO_TOKEN=..., "password": "..."
    # The optional quote before the separator matters: JSON keys are quoted, so
    # `"api_key": "..."` has a `"` between the name and the colon.
    (
        "assigned-secret",
        re.compile(
            r"(?i)\b(?P<name>[A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|APIKEY|API_KEY|PRIVATE_KEY|CREDENTIAL)[A-Z0-9_]*)"
            r"[\"']?\s*[:=]\s*(?P<quote>[\"']?)(?P<value>[^\s\"',;]{8,})"
        ),
    ),
    # Connection strings leak credentials in the authority component. The scheme starts only
    # where a run of scheme characters starts: anchored on `\b` alone, every dot in `a.a.a…`
    # began a fresh scan of the rest of the run, so the time went with the SQUARE of its
    # length — a 20 KB Write took a second in the gate that scans every write, 40 KB 3.6 s.
    # The user may be EMPTY: Redis names only a password, `redis://:<password>@host`, and a
    # production one was written into a signal file whole.
    ("url-credentials", re.compile(
        r"(?<![a-z0-9+.\-])\b([a-z][a-z0-9+.\-]*)://"
        r"(?P<user>[^\s:/@]*):(?P<secret>[^\s:/@]+)@(?P<host>[^\s:/@]+)")),
]

# What an HTTP header carries by its name: `Authorization: Basic …`, an API key, a cookie.
# Only `Bearer` was known, so a signal quoting the headers of a failed request was written
# down with the credential in it. Scrubbed and never refused: read by the header's name,
# a value has no shape of its own to be told from prose by, and one word too many taken
# out of a note costs nothing where a refused write costs the turn. A value is taken when
# a scheme names it or it carries a digit, and never when it is a template — `${TOKEN}`.
# The digit is looked for within a bounded stretch: unbounded, every `cookie:` in a line of
# them scanned to the end of the line, and the time went with the square of its length.
_HEADER_CREDENTIAL = re.compile(
    r"(?i)\b(?P<name>(?:proxy-)?authorization|x-api-key|api-key|x-auth-token|set-cookie|cookie)"
    r"(?P<sep>[\"']?[ \t]*[:=][ \t]*[\"']?)"
    r"(?P<value>(?:(?:basic|bearer|token|digest)[ \t]+[^\s\"'$<{]{6,}|[^\"'$<{\r\n]{0,1024}\d)"
    r"[^\r\n\"']*)"
)

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


# A number is not a credential. `TOKEN` is in the name list above and belongs there, but
# it is also the most common word in a machine-learning metric: `tokens_per_second`,
# `val_token_acc`, `loss_per_token`. Assigned a value of eight characters or more —
# `1043.7712` — every one of them read as an assigned secret, and the gate refused the
# command that was reading a training log. Reported from an eleven-hour measuring session.
#
# Safe by construction rather than by judgement: no credential worth rotating is a bare
# number, so this cannot suppress a real one.
_MEASUREMENT = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _is_measurement(value: str) -> bool:
    return bool(_MEASUREMENT.match(value.strip().strip("\"'")))


# A dotted code reference is not a literal, and only a literal can be a credential worth
# rotating. `TOKEN` is in the name list above and belongs there, but in machine-learning
# code it is overwhelmingly a generation limit read off a config object:
# `max_tokens=args.max_new_tokens`, `{"max_tokens": args.max_new_tokens}`. Every one of
# those read as an assigned secret, which made the inference code of a real project
# un-editable — and the issue reporting it could not be filed either, because quoting the
# gate's own message tripped the same gate (#138).
#
# Segments must each be an identifier, so `wJalrXUtnFEMI/K7…` and `AKIA…` are untouched.
# UNQUOTED only, which is what separates `args.max_new_tokens` from `"hunter2.correct"`.
_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+[)\]},]*$")


def _is_reference(value: str) -> bool:
    return bool(_REFERENCE.match(value.strip()))


# Brackets are code, and code is not a credential. A secret worth rotating is a LITERAL:
# a run of characters a service will accept verbatim, and no service accepts `useMemo(()`.
# `const tokens = useMemo(...)` was refused as an assigned secret — the name matched, the
# value was eight characters of an ordinary React expression — so the founder renamed a
# variable to get their file written, and the issue reporting it was refused for quoting
# the line (#205).
#
# UNQUOTED only, like `_is_reference`, because that is the whole discriminator: a quoted
# value is a literal whatever punctuation it carries. Residual cost, stated: an unquoted
# password containing a bracket in a `.env` file is missed. It joins the unquoted dotted
# one already documented below, and the direction is the same — a scanner that makes
# ordinary code un-editable is a scanner that gets worked around.
_BRACKETED = re.compile(r"[()\[\]{}]")


def _is_expression(value: str) -> bool:
    return bool(_BRACKETED.search(value))


# An address is where a credential is SENT, not the credential. `TOKEN_URL =
# "https://oauth2.googleapis.com/token"` names the endpoint every OAuth client posts to, and
# was refused as an assigned secret. Only a bare one: a URL carrying userinfo is
# `url-credentials`' to judge, and one carrying a query may carry a token in it.
_ADDRESS = re.compile(r"(?i)^https?://[^\s/?#@]+(?:/[^\s?#@]*)?$")


def _is_address(value: str) -> bool:
    return bool(_ADDRESS.match(value.strip()))


def _is_development_default(name: str, value: str) -> bool:
    """The assignment form of the default `_is_local_default` already lets through.

    `postgres://postgres:postgres@localhost` passed as a URL while `POSTGRES_PASSWORD:
    postgres` — the same default, spelled as the variable the image reads — was refused in
    a compose file. The same two shapes, judged the same way: a placeholder word, or a
    value that only repeats the name of what it unlocks, which is the user-equals-password
    of this form.
    """
    word = value.strip().lower()
    return _is_placeholder(word) or word in name.lower().replace("-", "_").split("_")


def _is_not_a_secret(match: "re.Match") -> bool:
    """Values the assignment form matches that cannot be credentials.

    Takes the match rather than the value because one of the three tests needs to know
    whether the value was QUOTED, and that is the only thing separating a reference to a
    number from a password that happens to contain a dot.

    Residual cost, stated rather than hidden: an unquoted lowercase dotted password in a
    `.env` file is missed. Narrow, and the direction is chosen deliberately — a scanner
    that makes ordinary code un-editable gets worked around, and a worked-around scanner
    protects nothing at all.
    """
    value = match.group("value")
    if _is_indirection(value) or _is_measurement(value) or _is_address(value):
        return True
    if _is_development_default(match.group("name"), value):
        return True
    if match.group("quote"):
        return False
    return _is_reference(value) or _is_expression(value)


def scrub(text: str) -> str:
    """Return `text` with anything that looks like a credential replaced."""
    if not text:
        return text
    out = text
    for name, pattern in _PATTERNS:
        if name == "assigned-secret":
            out = pattern.sub(
                lambda m: m.group(0) if _is_not_a_secret(m) else f"{m.group('name')}={REDACTED}",
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
    return _HEADER_CREDENTIAL.sub(lambda m: f"{m.group('name')}{m.group('sep')}{REDACTED}", out)


def find(text: str) -> list[str]:
    """Names of the detectors that fired. Used by gates that must refuse a write."""
    hits: set[str] = set()
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text or ""):
            if name == "assigned-secret" and _is_not_a_secret(match):
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
