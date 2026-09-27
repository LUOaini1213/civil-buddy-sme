"""Default session storage shared by entry points and freshly spawned tools.

An explicitly activated CLI job still overrides these module defaults with its
own .civil-buddy/out folder. With no environment override the legacy location
is unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path


def default_out_root(repo_root: Path) -> Path:
    configured = os.environ.get("CIVIL_OUT_ROOT")
    return Path(configured).expanduser().resolve() if configured else repo_root / "demo" / "out"
