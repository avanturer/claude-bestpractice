# Pattern catalogue

Loaded on demand. Each entry is a before/after with the reason it matters to a model
reader.

---

## Signature restatement

```python
# BAD — every line is derivable from the signature and rots on its own schedule
def fetch(url: str, timeout: float = 5.0) -> Response:
    """Fetch a URL.

    Args:
        url: The URL to fetch.
        timeout: Timeout in seconds. Defaults to 5.0.

    Returns:
        The response.
    """
```

```python
# GOOD — nothing restated; only what the signature cannot say
def fetch(url: str, timeout: float = 5.0) -> Response:
    """Raises on 4xx/5xx rather than returning them: every caller treats an error
    status as fatal, and two of them previously forgot to check.
    """
```

---

## Narration

```python
# BAD
# Step 1: get the user
user = db.get_user(uid)
# Step 2: check permissions
if not user.can_edit(doc):
    # Step 3: raise
    raise Forbidden()
```

```python
# GOOD — no comment at all. The code says this.
user = db.get_user(uid)
if not user.can_edit(doc):
    raise Forbidden()
```

Narration is pure cost: it is re-read on every context load, it never disambiguates
anything, and it competes for attention with the two comments in the file that matter.

---

## The comment that earns its place

```python
def rank(candidates: list[Doc], query: str) -> list[Doc]:
    """Ties break by insertion order, not by score.

    The upstream ranker is non-deterministic on equal scores, and the diff-based
    tests downstream compare exact ordering. Stable ties are load-bearing; do not
    swap this for `sorted(..., reverse=True)` without fixing those tests first.
    """
```

Non-derivable, names the constraint, and tells the next reader what breaks. This is the
shape to aim for.

---

## Invariants belong in code, not prose

```python
# WEAK — a claim nobody checks
def transfer(src: Account, dst: Account, amount: Decimal) -> None:
    """Amount must be positive and src must have sufficient balance."""
```

```python
# STRONG — the invariant is enforced, and the docstring covers what enforcement cannot
def transfer(src: Account, dst: Account, amount: Decimal) -> None:
    """Not atomic across accounts: the caller holds the transaction.

    Called from two places that already own a transaction; adding one here caused a
    deadlock against the ledger writer.
    """
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount}")
    if src.balance < amount:
        raise InsufficientFunds(src.id, amount, src.balance)
```

A prose precondition is advisory. An assertion is binding. The same distinction that
governs the whole plugin governs a single function.

---

## Module headers

```python
# BAD — derivable, and the harness's own doctor trims exactly this
"""User module.

Contains:
    - User
    - UserRepository
    - get_user()
"""
```

```python
# GOOD — orientation that a file listing cannot give
"""User identity and lookup.

`User.id` is the external, stable identifier and appears in URLs; `User.pk` is the
database key and must never leave this module. Conflating them caused the account
enumeration incident.
"""
```

---

## Types that lie

```python
# BAD — checked, believed, and wrong
def parse(raw: str) -> dict:
    if raw.startswith("["):
        return json.loads(raw)      # a list
    if not raw:
        return None                 # not a dict either
    return json.loads(raw)
```

```python
# GOOD
def parse(raw: str) -> dict | list | None:
    ...
```

A wrong annotation is worse than none: the reader stops checking. Being imprecise is
allowed; being wrong is not.

---

## Workarounds

```python
# BAD
time.sleep(0.1)  # needed for some reason
```

```python
# GOOD
# The watcher emits CREATE before the file is flushed, so an immediate read sees zero
# bytes. Polling for a non-empty file is worse: an intentionally empty file then hangs.
time.sleep(0.1)
```

An unexplained workaround gets deleted by the next session, the bug returns, and the
workaround gets re-added. Naming the constraint ends that cycle.

---

## TODO discipline

```python
# BAD — no owner, no condition, immortal
# TODO: make this faster
```

```python
# GOOD — removable by anyone who can check the condition
# TODO(perf): batch these once the ingest endpoint accepts arrays.
# Remove when `POST /v2/ingest` ships — tracked in decisions/0007-ingest-batching.md
```

A TODO that cannot be closed by an outsider is a permanent comment wearing a disguise.
