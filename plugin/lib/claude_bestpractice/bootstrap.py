"""Make `claude_bestpractice` importable from an entry point, wherever the plugin was installed.

The plugin root path changes on every update, so nothing may hardcode it. Resolving
from `__file__` is the only stable anchor.
"""

from __future__ import annotations

import sys
from pathlib import Path


def install() -> None:
    lib = Path(__file__).resolve().parent.parent
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
