"""Import the shared fixtures so the sandbox HOME is set for pytest too.

The sandbox itself lives in `helpers.py`, because `make test` runs unittest and never loads
a conftest — and that is the runner the release gate uses (#121).
"""

import helpers  # noqa: F401
