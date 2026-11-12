---
title: A connection lost in a test's body is no verdict either
paths: plugin/lib/claude_bestpractice/evidence.py, plugin/lib/claude_bestpractice/witness.py, plugin/lib/claude_bestpractice/migrate.py
date: 2026-11-12
---

## Decision
**What a test broke on says whether the code answered, never pytest's phase.** A FAILURE whose
exception says a connection to a server was lost or refused (`DISCONNECTED`) counts as an ERROR
does: it is run once more beside the ERRORs (decision 0034), and a run in which every test that
broke lost its connection, at setup or in its body, and did again on its own, is no verdict on
the code (decision 0067). A failed assertion never is, whatever it quotes: `AssertionError`, a
rewritten `assert`, `pytest.fail`. One FAILURE on anything else makes the run a verdict, and
nothing in it is run twice. This revises 0067's "ERRORed" and 0034's "broke in setup, teardown
or collection".

**A red record an older gate filed over such a run goes on the upgrade**: repair 0044, at its
second revision, read off the reports it kept.

## Why
> parallel workers lost connections (server closed the connection unexpectedly): 54 errors and
> 1 failure in test_npd, test_offline_api, test_offline_build, reported as a failing …

The one FAILURE was the same libpq message, raised where the test body first queried rather than
where a fixture connected. Counted as the code's, it turned off the second run and decision 0067
both, and the run was filed red over a database behind Docker's proxy on WSL (#355, #339).

## Rejected
- **Any FAILURE beside lost connections as no verdict.** An assertion is the code answering.
- **A disconnect found in the traceback below an exception of the code's.** 0067 stands: the
  message, never the traceback.
- **The exception's type from the report.** pytest's JUnit writes none; its message starts with
  the exception's name, and that is what is read.

## Cost accepted
Code under test that loses its own connection in a test's body finishes UNVERIFIED rather than
red, as 0067 accepted for a fixture. An `AssertionError` subclass under its own name reads as an
exception.
