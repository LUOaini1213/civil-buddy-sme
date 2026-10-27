"""Native filesystem spelling, separate from authorization and identity paths."""
from __future__ import annotations

import os
from pathlib import Path


def native_path(path: Path) -> Path:
    """Keep the same absolute directory while enabling long Windows I/O paths.

    Callers still own containment, link and permission checks. This function
    neither grants access nor changes configured workspace/state ownership.
    """
    absolute = Path(os.path.abspath(path))
    value = str(absolute)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return absolute
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)
