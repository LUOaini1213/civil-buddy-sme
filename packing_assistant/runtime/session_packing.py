"""Session packing snapshots. Projection only — never a second packer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[2]
from packing_assistant.runtime.paths import default_out_root
_DIR = default_out_root(_ROOT)


def _path(session_id: str) -> Path:
    sid = (session_id or "default").replace("..", "_")
    return _DIR / sid / "packing_summary.json"


def save_packing_snapshot(session_id: str, summary: Optional[Dict[str, Any]]) -> None:
    if not isinstance(summary, dict) or not summary:
        return
    path = _path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    from packing_assistant.sandbox import guarded_write_text

    guarded_write_text(path, json.dumps(summary, ensure_ascii=False, default=str))


def load_packing_snapshot(session_id: str) -> Optional[Dict[str, Any]]:
    path = _path(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data else None
