"""T010: session.summary slots for jurisdiction / project / P0. Compress is marked, not pretended.

Civil Buddy owns context. DeepSeek is a stateless completion API — assemble a short
turn from these slots, do not dump chat history as facts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from packing_assistant.jurisdiction import infer_jurisdiction

_ROOT = Path(__file__).resolve().parents[2]
from packing_assistant.runtime.paths import default_out_root
_OUT = default_out_root(_ROOT)
DEFAULT_PROJECT = "幕墙项目投标应答（草稿）"
DROPPED = "更早对话已压缩，细节标 [A001] / UNSPECIFIED，不要假装读过。"


def _safe(session_id: str) -> str:
    # one rule for a session's folder name, shared with the link record, the packing snapshot and the job files
    # (the old rule here kept '.', so session "." wrote into demo/out itself)
    from packing_assistant.runtime.agent_loop import _safe_sid

    return _safe_sid(session_id)


def summary_path(session_id: str) -> Path:
    return _OUT / _safe(session_id) / "session.summary.json"


def save_summary(
    session_id: str,
    *,
    jurisdiction: str = "",
    project: str = "",
    p0_confirmed: bool = False,
    compressed: bool = False,
    dropped_note: str = "",
) -> Path:
    path = summary_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "jurisdiction": jurisdiction or "UNSPECIFIED",
        "project": project or "UNSPECIFIED",
        "p0_confirmed": p0_confirmed is True,
        "compressed": bool(compressed),
        "dropped_note": dropped_note
        or (DROPPED if compressed else ""),
    }
    from packing_assistant.sandbox import guarded_write_text

    guarded_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def load_summary(session_id: str) -> Optional[Dict[str, Any]]:
    path = summary_path(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _real_project(name: str) -> str:
    n = (name or "").strip()
    if not n or n == DEFAULT_PROJECT or n == "UNSPECIFIED":
        return ""
    return n


def assemble_context(
    session_id: str,
    *,
    text: str = "",
    project_name: str = "",
    p0_confirmed: bool = False,
    compressed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Merge this request onto disk slots. Sticky: project, compressed True. p0 is this request's alone."""
    prev = load_summary(session_id) or {}
    jur = infer_jurisdiction(text, str(prev.get("jurisdiction") or ""))
    project = _real_project(project_name) or _real_project(str(prev.get("project") or "")) or "UNSPECIFIED"
    # 确认句只管本轮：上一轮打过，不等于这一轮的高风险写盘也有人签认。
    p0 = p0_confirmed is True
    comp = bool(prev.get("compressed")) if compressed is None else bool(compressed)
    note = str(prev.get("dropped_note") or "")
    if comp and not note:
        note = DROPPED
    ctx = {
        "jurisdiction": jur,
        "project": project,
        "p0_confirmed": p0,
        "compressed": comp,
        "dropped_note": note if comp else "",
        "has_handoff": False,
        "has_packing": False,
    }
    from packing_assistant.runtime.session_handoff import load_handoff
    from packing_assistant.runtime.session_packing import load_packing_snapshot

    ctx["has_handoff"] = bool(load_handoff(session_id))
    ctx["has_packing"] = bool(load_packing_snapshot(session_id))
    save_summary(
        session_id,
        jurisdiction=jur,
        project=project,
        p0_confirmed=p0,
        compressed=comp,
        dropped_note=ctx["dropped_note"],
    )
    return ctx


def prompt_prefix(ctx: Optional[Dict[str, Any]]) -> str:
    """Short block for chat/run. Not a transcript. Not 66-expert KB."""
    if not isinstance(ctx, dict) or not ctx:
        return ""
    lines = [
        f"本会话槽：辖区={ctx.get('jurisdiction') or 'UNSPECIFIED'}；项目={ctx.get('project') or 'UNSPECIFIED'}。"
        "事实以本槽与工具为准，不要用模型记忆补数字。"
    ]
    if ctx.get("has_handoff"):
        lines.append("本 session 有 tender.handoff.json，合规/技术岗只读该交接。")
    if ctx.get("has_packing"):
        lines.append("本 session 有 packing_summary.json，pack-ship 只抄、不重算 xyz。")
    if ctx.get("compressed") and ctx.get("dropped_note"):
        lines.append(str(ctx["dropped_note"]))
    return "\n".join(lines)
