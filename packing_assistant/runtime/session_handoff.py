"""Session tender.handoff.v1 snapshot. Shared by bid-parse / compliance / tech."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[2]
from packing_assistant.runtime.paths import default_out_root
_DIR = default_out_root(_ROOT)


def _safe(session_id: str) -> str:
    return (session_id or "default").replace("..", "_").replace("/", "_").replace("\\", "_") or "default"


def handoff_path(session_id: str) -> Path:
    return _DIR / _safe(session_id) / "tender.handoff.json"


def save_handoff(session_id: str, handoff: Optional[Dict[str, Any]]) -> Optional[Path]:
    if not isinstance(handoff, dict) or not handoff:
        return None
    path = handoff_path(session_id)
    from packing_assistant.sandbox import guarded_write_text

    guarded_write_text(path, json.dumps(handoff, ensure_ascii=False, indent=2, default=str))
    return path


def load_handoff(session_id: str) -> Optional[Dict[str, Any]]:
    path = handoff_path(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data else None
